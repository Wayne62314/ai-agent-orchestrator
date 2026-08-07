use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    env,
    ffi::OsString,
    fs,
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicU64, Ordering},
        mpsc::{self, Receiver},
        Arc, Mutex,
    },
    thread,
    time::Duration,
};
use tauri::Manager;

const PROTOCOL: &str = "aiao.desktop.v1";
const MAX_MESSAGE_BYTES: usize = 1_048_576;
const RESPONSE_TIMEOUT: Duration = Duration::from_secs(30);

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
struct UiRequest {
    protocol: String,
    method: String,
    #[serde(default)]
    params: Value,
}

struct DesktopState {
    manager: Arc<Mutex<SidecarManager>>,
    codex_window: Mutex<CodexWindowState>,
}

impl DesktopState {
    fn new(spec: SidecarSpec) -> Self {
        Self {
            manager: Arc::new(Mutex::new(SidecarManager::new(spec))),
            codex_window: Mutex::new(CodexWindowState::default()),
        }
    }

    fn warm_up(&self) {
        let manager = Arc::clone(&self.manager);
        let _ = thread::Builder::new()
            .name("aiao-sidecar-warm-up".to_string())
            .spawn(move || {
                let request = UiRequest {
                    protocol: PROTOCOL.to_string(),
                    method: "system/initialize".to_string(),
                    params: json!({}),
                };
                if let Ok(mut manager) = manager.lock() {
                    let _ = manager.request(request);
                }
            });
    }
}

#[derive(Clone)]
struct SidecarSpec {
    program: OsString,
    arguments: Vec<OsString>,
}

impl SidecarSpec {
    fn discover(data_root: PathBuf, packaged_sidecar: Option<PathBuf>) -> Self {
        let database = data_root.join("state.db");
        let database_arg = database.into_os_string();
        let data_root_arg = data_root.into_os_string();
        if let Some(executable) = env::var_os("AIAO_SIDECAR_PATH") {
            return Self {
                program: executable,
                arguments: vec![
                    OsString::from("--db"),
                    database_arg,
                    OsString::from("--data-root"),
                    data_root_arg,
                ],
            };
        }
        if let Some(executable) = packaged_sidecar.filter(|path| path.is_file()) {
            return Self {
                program: executable.into_os_string(),
                arguments: vec![
                    OsString::from("--db"),
                    database_arg,
                    OsString::from("--data-root"),
                    data_root_arg,
                ],
            };
        }
        Self {
            program: env::var_os("AIAO_PYTHON").unwrap_or_else(|| OsString::from("python")),
            arguments: vec![
                OsString::from("-m"),
                OsString::from("agent_orchestrator.desktop_rpc"),
                OsString::from("--db"),
                database_arg,
                OsString::from("--data-root"),
                data_root_arg,
            ],
        }
    }
}

struct SidecarManager {
    spec: SidecarSpec,
    process: Option<SidecarProcess>,
    next_id: AtomicU64,
}

impl SidecarManager {
    fn new(spec: SidecarSpec) -> Self {
        Self {
            spec,
            process: None,
            next_id: AtomicU64::new(1),
        }
    }

    fn request(&mut self, request: UiRequest) -> Result<Value, String> {
        validate_ui_request(&request)?;
        let request_id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let message = json!({
            "protocol": PROTOCOL,
            "id": request_id,
            "method": request.method,
            "params": request.params,
        });
        let encoded = serde_json::to_vec(&message)
            .map_err(|_| "The desktop request could not be encoded.".to_string())?;
        if encoded.len() > MAX_MESSAGE_BYTES {
            return Err("The desktop request is larger than 1 MiB.".to_string());
        }
        if self.process.is_none() {
            self.process = Some(SidecarProcess::spawn(&self.spec)?);
        }
        let result = self
            .process
            .as_mut()
            .expect("sidecar process is initialized")
            .request(request_id, &encoded);
        match result {
            Ok(value) => Ok(value),
            Err(error) => {
                let restart = should_restart_after(&error);
                let message = match error {
                    SidecarRequestError::Application(message)
                    | SidecarRequestError::Transport(message) => message,
                };
                if restart {
                    if let Some(mut process) = self.process.take() {
                        process.stop();
                    }
                }
                Err(message)
            }
        }
    }
}

enum SidecarRequestError {
    Application(String),
    Transport(String),
}

fn should_restart_after(error: &SidecarRequestError) -> bool {
    matches!(error, SidecarRequestError::Transport(_))
}

struct SidecarProcess {
    child: Child,
    stdin: ChildStdin,
    responses: Receiver<String>,
}

impl SidecarProcess {
    fn spawn(spec: &SidecarSpec) -> Result<Self, String> {
        let mut command = Command::new(&spec.program);
        command
            .args(&spec.arguments)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0800_0000);
        }
        let mut child = command
            .spawn()
            .map_err(|_| "The local Python sidecar could not be started.".to_string())?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| "The sidecar input channel is unavailable.".to_string())?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| "The sidecar output channel is unavailable.".to_string())?;
        let (sender, responses) = mpsc::channel();
        thread::Builder::new()
            .name("aiao-sidecar-output".to_string())
            .spawn(move || {
                for line in BufReader::new(stdout).lines() {
                    let Ok(line) = line else {
                        break;
                    };
                    if sender.send(line).is_err() {
                        break;
                    }
                }
            })
            .map_err(|_| "The sidecar response reader could not start.".to_string())?;
        Ok(Self {
            child,
            stdin,
            responses,
        })
    }

    fn request(&mut self, request_id: u64, encoded: &[u8]) -> Result<Value, SidecarRequestError> {
        self.stdin
            .write_all(encoded)
            .and_then(|_| self.stdin.write_all(b"\n"))
            .and_then(|_| self.stdin.flush())
            .map_err(|_| {
                SidecarRequestError::Transport(
                    "The sidecar request channel closed unexpectedly.".to_string(),
                )
            })?;
        let raw = self.responses.recv_timeout(RESPONSE_TIMEOUT).map_err(|_| {
            SidecarRequestError::Transport(
                "The local sidecar did not respond within 30 seconds.".to_string(),
            )
        })?;
        if raw.len() > MAX_MESSAGE_BYTES {
            return Err(SidecarRequestError::Transport(
                "The sidecar response is larger than 1 MiB.".to_string(),
            ));
        }
        let response: Value = serde_json::from_str(&raw).map_err(|_| {
            SidecarRequestError::Transport("The sidecar returned an invalid response.".to_string())
        })?;
        if response.get("protocol").and_then(Value::as_str) != Some(PROTOCOL)
            || response.get("id").and_then(Value::as_u64) != Some(request_id)
        {
            return Err(SidecarRequestError::Transport(
                "The sidecar response did not match the request.".to_string(),
            ));
        }
        if let Some(error) = response.get("error") {
            let code = error
                .get("code")
                .and_then(Value::as_str)
                .unwrap_or("REQUEST_REJECTED");
            let message = error
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("The local operation was rejected.");
            return Err(SidecarRequestError::Application(format!(
                "{code}: {message}"
            )));
        }
        response.get("result").cloned().ok_or_else(|| {
            SidecarRequestError::Transport("The sidecar response has no result.".to_string())
        })
    }

    fn stop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for SidecarProcess {
    fn drop(&mut self) {
        self.stop();
    }
}

fn validate_ui_request(request: &UiRequest) -> Result<(), String> {
    if request.protocol != PROTOCOL {
        return Err("The desktop protocol version is not supported.".to_string());
    }
    if !request.params.is_object() {
        return Err("Desktop request parameters must be an object.".to_string());
    }
    if !allowed_method(&request.method) {
        return Err("This desktop operation is not allowed.".to_string());
    }
    Ok(())
}

fn allowed_method(method: &str) -> bool {
    matches!(
        method,
        "system/initialize"
            | "system/status"
            | "task/list"
            | "task/read"
            | "task/codex-thread"
            | "task/detail"
            | "task/create"
            | "task/start"
            | "task/pause"
            | "task/resume"
            | "task/cancel"
            | "approval/list"
            | "approval/decide"
            | "account/read"
            | "account/login/start"
            | "account/login/status"
            | "account/login/cancel"
            | "account/logout"
            | "repository/inspect"
            | "maintenance/read"
            | "maintenance/backup"
            | "maintenance/restore"
            | "maintenance/diagnostics"
    )
}

impl Drop for DesktopState {
    fn drop(&mut self) {
        #[cfg(windows)]
        if let Ok(mut state) = self.codex_window.lock() {
            let _ = windows_dock::detach(&mut state);
        }
    }
}

#[derive(Default)]
struct CodexWindowState {
    attached: bool,
    window: isize,
    original_left: i32,
    original_top: i32,
    original_width: i32,
    original_height: i32,
    candidate_window: isize,
    gesture_window: isize,
    gesture_start_left: i32,
    gesture_start_top: i32,
    gesture_active: bool,
    gesture_moved: bool,
    drop_permitted: bool,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct DockRect {
    x: i32,
    y: i32,
    width: i32,
    height: i32,
}

#[tauri::command]
fn open_codex_thread(thread_id: String) -> Result<(), String> {
    if thread_id.is_empty()
        || !thread_id
            .chars()
            .all(|value| value.is_ascii_alphanumeric() || value == '-')
    {
        return Err("The Codex task id is invalid.".to_string());
    }
    #[cfg(windows)]
    {
        windows_dock::open_thread(&thread_id)
    }
    #[cfg(not(windows))]
    {
        Err("Codex window docking currently requires Windows.".to_string())
    }
}

#[tauri::command]
fn codex_dock_poll(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, DesktopState>,
    rect: DockRect,
) -> Result<Value, String> {
    #[cfg(windows)]
    {
        let parent = window.hwnd().map_err(|error| error.to_string())?;
        let mut dock = state
            .codex_window
            .lock()
            .map_err(|_| "The Codex window state is unavailable.".to_string())?;
        windows_dock::poll(parent.0 as isize, &mut dock, rect)
    }
    #[cfg(not(windows))]
    {
        let _ = (window, state, rect);
        Ok(
            json!({"found": false, "attached": false, "near": false, "leftButtonDown": false, "dropReady": false}),
        )
    }
}

#[tauri::command]
fn attach_codex_window(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, DesktopState>,
    rect: DockRect,
) -> Result<Value, String> {
    #[cfg(windows)]
    {
        let parent = window.hwnd().map_err(|error| error.to_string())?;
        let mut dock = state
            .codex_window
            .lock()
            .map_err(|_| "The Codex window state is unavailable.".to_string())?;
        windows_dock::attach(parent.0 as isize, &mut dock, rect)?;
        Ok(json!({"attached": true}))
    }
    #[cfg(not(windows))]
    {
        let _ = (window, state, rect);
        Err("Codex window docking currently requires Windows.".to_string())
    }
}

#[tauri::command]
fn detach_codex_window(state: tauri::State<'_, DesktopState>) -> Result<Value, String> {
    #[cfg(windows)]
    {
        let mut dock = state
            .codex_window
            .lock()
            .map_err(|_| "The Codex window state is unavailable.".to_string())?;
        windows_dock::detach(&mut dock)?;
        Ok(json!({"attached": false}))
    }
    #[cfg(not(windows))]
    {
        let _ = state;
        Ok(json!({"attached": false}))
    }
}

#[tauri::command]
async fn sidecar_request(
    state: tauri::State<'_, DesktopState>,
    request: UiRequest,
) -> Result<Value, String> {
    let manager = Arc::clone(&state.manager);
    tauri::async_runtime::spawn_blocking(move || {
        manager
            .lock()
            .map_err(|_| "The local sidecar manager is unavailable.".to_string())?
            .request(request)
    })
    .await
    .map_err(|_| "The local sidecar task stopped unexpectedly.".to_string())?
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let data_root = app.path().app_data_dir()?;
            fs::create_dir_all(&data_root)?;
            let packaged_sidecar = app
                .path()
                .resource_dir()?
                .join("agent-orchestrator-sidecar.exe");
            let desktop_state =
                DesktopState::new(SidecarSpec::discover(data_root, Some(packaged_sidecar)));
            desktop_state.warm_up();
            app.manage(desktop_state);
            Ok(())
        })
        .on_window_event(|window, event| {
            #[cfg(windows)]
            if matches!(event, tauri::WindowEvent::Focused(true)) {
                if let Ok(parent) = window.hwnd() {
                    let desktop = window.state::<DesktopState>();
                    let dock = desktop.codex_window.lock();
                    if let Ok(dock) = dock {
                        let _ = windows_dock::raise_attached(parent.0 as isize, &dock);
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            sidecar_request,
            open_codex_thread,
            codex_dock_poll,
            attach_codex_window,
            detach_codex_window
        ])
        .run(tauri::generate_context!())
        .expect("failed to run AI Agent Orchestrator");
}

#[cfg(windows)]
mod windows_dock {
    use super::{CodexWindowState, DockRect};
    use serde_json::{json, Value};
    use std::{mem, ptr};
    use windows_sys::Win32::{
        Foundation::{CloseHandle, HWND, LPARAM, POINT, RECT},
        Graphics::Gdi::ClientToScreen,
        System::Threading::{
            OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
        },
        UI::{
            Input::KeyboardAndMouse::{GetAsyncKeyState, VK_LBUTTON},
            Shell::ShellExecuteW,
            WindowsAndMessaging::{
                EnumWindows, GetForegroundWindow, GetWindowRect, GetWindowTextLengthW,
                GetWindowThreadProcessId, IsIconic, IsWindow, IsWindowVisible, SetForegroundWindow,
                SetWindowPos, ShowWindow, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER,
                SWP_SHOWWINDOW, SW_RESTORE,
            },
        },
    };

    pub fn open_thread(thread_id: &str) -> Result<(), String> {
        let target = wide(&format!("codex://threads/{thread_id}"));
        let operation = wide("open");
        let result = unsafe {
            ShellExecuteW(
                ptr::null_mut(),
                operation.as_ptr(),
                target.as_ptr(),
                ptr::null(),
                ptr::null(),
                SW_RESTORE,
            )
        } as isize;
        if result <= 32 {
            return Err("Windows could not open the Codex task.".to_string());
        }
        Ok(())
    }

    pub fn poll(
        parent: isize,
        state: &mut CodexWindowState,
        rect: DockRect,
    ) -> Result<Value, String> {
        let left_down = unsafe { GetAsyncKeyState(VK_LBUTTON as i32) } < 0;
        if state.attached {
            let hwnd = state.window as HWND;
            if unsafe { IsWindow(hwnd) } == 0 {
                *state = CodexWindowState::default();
            } else {
                if unsafe { IsIconic(parent as HWND) } == 0 {
                    position(parent as HWND, hwnd, &rect)?;
                }
                return Ok(json!({
                    "found": true,
                    "attached": true,
                    "near": false,
                    "leftButtonDown": left_down,
                    "dropReady": false
                }));
            }
        }
        let Some(hwnd) = find_codex_window() else {
            return Ok(json!({
                "found": false,
                "attached": false,
                "near": false,
                "leftButtonDown": left_down,
                "dropReady": false
            }));
        };
        let mut window_rect: RECT = unsafe { mem::zeroed() };
        if unsafe { GetWindowRect(hwnd, &mut window_rect) } == 0 {
            return Err("Windows could not read the Codex window position.".to_string());
        }
        let target = screen_rect(parent as HWND, &rect)?;
        let distance = rectangle_distance(&window_rect, &target);
        state.candidate_window = hwnd as isize;
        let drop_ready = update_drag_gesture(state, hwnd, &window_rect, left_down, distance <= 72);
        Ok(json!({
            "found": true,
            "attached": false,
            "near": state.gesture_active && state.gesture_moved && distance <= 72,
            "leftButtonDown": left_down,
            "dropReady": drop_ready
        }))
    }

    pub fn attach(
        parent: isize,
        state: &mut CodexWindowState,
        rect: DockRect,
    ) -> Result<(), String> {
        if state.attached {
            return position(parent as HWND, state.window as HWND, &rect);
        }
        if !state.drop_permitted {
            return Err(
                "Drag the official Codex window onto the socket before attaching it.".to_string(),
            );
        }
        state.drop_permitted = false;
        let hwnd = state.candidate_window as HWND;
        if hwnd.is_null() || unsafe { IsWindow(hwnd) } == 0 || !is_official_codex(hwnd) {
            return Err("No dragged official Codex window was found.".to_string());
        }
        let mut original: RECT = unsafe { mem::zeroed() };
        if unsafe { GetWindowRect(hwnd, &mut original) } == 0 {
            return Err("Windows could not preserve the Codex window position.".to_string());
        }
        // Keep Codex as a real top-level window. Cross-process SetParent embedding
        // breaks reliable keyboard focus and clips the part that must protrude
        // above the Agent Dock base.
        state.attached = true;
        state.window = hwnd as isize;
        state.original_left = original.left;
        state.original_top = original.top;
        state.original_width = original.right - original.left;
        state.original_height = original.bottom - original.top;
        unsafe { ShowWindow(hwnd, SW_RESTORE) };
        position(parent as HWND, hwnd, &rect)?;
        // Positioning can happen while another window owns the foreground. Do
        // one explicit z-order pass after the attach so the real Codex window
        // is immediately above Agent Dock even in that case.
        raise_attached(parent as isize, state)?;
        unsafe { SetForegroundWindow(hwnd) };
        Ok(())
    }

    pub fn detach(state: &mut CodexWindowState) -> Result<(), String> {
        if !state.attached {
            return Ok(());
        }
        let hwnd = state.window as HWND;
        if unsafe { IsWindow(hwnd) } != 0 {
            unsafe {
                SetWindowPos(
                    hwnd,
                    ptr::null_mut(),
                    state.original_left,
                    state.original_top,
                    state.original_width.max(1),
                    state.original_height.max(1),
                    SWP_SHOWWINDOW,
                );
                ShowWindow(hwnd, SW_RESTORE);
            }
        }
        *state = CodexWindowState::default();
        Ok(())
    }

    pub fn raise_attached(parent: isize, state: &CodexWindowState) -> Result<(), String> {
        if !state.attached {
            return Ok(());
        }
        let hwnd = state.window as HWND;
        if unsafe { IsWindow(hwnd) } == 0 {
            return Ok(());
        }
        // `hWndInsertAfter` is the window that should precede the moved window
        // in z-order. The old implementation passed `parent` while moving the
        // Codex window, which placed Codex *behind* Agent Dock. Reorder the
        // parent after Codex instead; this keeps the real Codex above the dock
        // only while the pair is active, without making it globally topmost.
        let ok = unsafe {
            SetWindowPos(
                parent as HWND,
                hwnd,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            )
        };
        if ok == 0 {
            return Err("Windows could not keep Codex above Agent Dock.".to_string());
        }
        Ok(())
    }

    fn position(parent: HWND, hwnd: HWND, rect: &DockRect) -> Result<(), String> {
        let target = screen_rect(parent, rect)?;
        let width = (target.right - target.left).max(1);
        let height = (target.bottom - target.top).max(1);
        let foreground = unsafe { GetForegroundWindow() };
        let pair_active = foreground == parent || foreground == hwnd;
        let ok = unsafe {
            SetWindowPos(
                hwnd,
                ptr::null_mut(),
                target.left,
                target.top,
                width,
                height,
                SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_NOZORDER,
            )
        };
        if ok == 0 {
            return Err("Windows could not position the Codex window.".to_string());
        }
        if pair_active {
            let state = CodexWindowState {
                attached: true,
                window: hwnd as isize,
                ..CodexWindowState::default()
            };
            raise_attached(parent as isize, &state)?;
        }
        Ok(())
    }

    fn screen_rect(parent: HWND, rect: &DockRect) -> Result<RECT, String> {
        let mut point = POINT {
            x: rect.x,
            y: rect.y,
        };
        if unsafe { ClientToScreen(parent, &mut point) } == 0 {
            return Err("Windows could not locate the docking area.".to_string());
        }
        Ok(RECT {
            left: point.x,
            top: point.y,
            right: point.x + rect.width.max(1),
            bottom: point.y + rect.height.max(1),
        })
    }

    fn rectangle_distance(first: &RECT, second: &RECT) -> i32 {
        let dx = (second.left - first.right)
            .max(first.left - second.right)
            .max(0);
        let dy = (second.top - first.bottom)
            .max(first.top - second.bottom)
            .max(0);
        (((dx * dx + dy * dy) as f64).sqrt()) as i32
    }

    fn update_drag_gesture(
        state: &mut CodexWindowState,
        hwnd: HWND,
        window_rect: &RECT,
        left_down: bool,
        near: bool,
    ) -> bool {
        if left_down {
            if !state.gesture_active || state.gesture_window != hwnd as isize {
                state.gesture_active = true;
                state.gesture_window = hwnd as isize;
                state.gesture_start_left = window_rect.left;
                state.gesture_start_top = window_rect.top;
                state.gesture_moved = false;
                state.drop_permitted = false;
            } else if (window_rect.left - state.gesture_start_left).abs() >= 8
                || (window_rect.top - state.gesture_start_top).abs() >= 8
            {
                state.gesture_moved = true;
            }
            return false;
        }

        let released = state.gesture_active && state.gesture_moved && near;
        state.gesture_active = false;
        state.gesture_moved = false;
        if released {
            state.drop_permitted = true;
        }
        released
    }

    fn find_codex_window() -> Option<HWND> {
        let mut windows: Vec<HWND> = Vec::new();
        unsafe {
            EnumWindows(
                Some(collect_window),
                &mut windows as *mut Vec<HWND> as LPARAM,
            );
        }
        windows.into_iter().find(|hwnd| is_official_codex(*hwnd))
    }

    unsafe extern "system" fn collect_window(hwnd: HWND, value: LPARAM) -> i32 {
        if unsafe { IsWindowVisible(hwnd) } != 0 && unsafe { GetWindowTextLengthW(hwnd) } > 0 {
            unsafe { &mut *(value as *mut Vec<HWND>) }.push(hwnd);
        }
        1
    }

    fn is_official_codex(hwnd: HWND) -> bool {
        let mut process_id = 0u32;
        unsafe { GetWindowThreadProcessId(hwnd, &mut process_id) };
        let process = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, process_id) };
        if process.is_null() {
            return false;
        }
        let mut path = vec![0u16; 32768];
        let mut length = path.len() as u32;
        let read =
            unsafe { QueryFullProcessImageNameW(process, 0, path.as_mut_ptr(), &mut length) };
        unsafe { CloseHandle(process) };
        if read == 0 {
            return false;
        }
        let path = String::from_utf16_lossy(&path[..length as usize]).to_ascii_lowercase();
        is_official_codex_path(&path)
    }

    fn is_official_codex_path(path: &str) -> bool {
        let path = path.to_ascii_lowercase();
        path.contains("\\windowsapps\\openai.codex_") || path.contains("\\openai\\codex\\")
    }

    fn wide(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(std::iter::once(0)).collect()
    }

    #[cfg(test)]
    mod tests {
        use super::{is_official_codex_path, update_drag_gesture, CodexWindowState, HWND, RECT};

        #[test]
        fn accepts_the_official_windows_store_codex_package_even_when_executable_is_chatgpt() {
            assert!(is_official_codex_path(
                r"C:\Program Files\WindowsApps\OpenAI.Codex_26.721.11231.0_x64__2p2nqsd0c76g0\app\ChatGPT.exe"
            ));
        }

        #[test]
        fn rejects_unrelated_chatgpt_or_codex_named_executables() {
            assert!(!is_official_codex_path(
                r"C:\Users\Example\Downloads\Codex.exe"
            ));
        }

        #[test]
        fn stationary_click_cannot_authorize_a_drop() {
            let hwnd = 1isize as HWND;
            let rect = RECT {
                left: 100,
                top: 100,
                right: 900,
                bottom: 700,
            };
            let mut state = CodexWindowState::default();
            assert!(!update_drag_gesture(&mut state, hwnd, &rect, true, true));
            assert!(!update_drag_gesture(&mut state, hwnd, &rect, false, true));
            assert!(!state.drop_permitted);
        }

        #[test]
        fn moved_window_released_near_socket_authorizes_one_drop() {
            let hwnd = 1isize as HWND;
            let start = RECT {
                left: 100,
                top: 100,
                right: 900,
                bottom: 700,
            };
            let moved = RECT {
                left: 116,
                top: 105,
                right: 916,
                bottom: 705,
            };
            let mut state = CodexWindowState::default();
            assert!(!update_drag_gesture(&mut state, hwnd, &start, true, false));
            assert!(!update_drag_gesture(&mut state, hwnd, &moved, true, true));
            assert!(update_drag_gesture(&mut state, hwnd, &moved, false, true));
            assert!(state.drop_permitted);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command_allowlist_rejects_arbitrary_shell_or_database_methods() {
        assert!(allowed_method("task/pause"));
        assert!(allowed_method("task/codex-thread"));
        assert!(allowed_method("approval/decide"));
        assert!(allowed_method("account/login/start"));
        assert!(allowed_method("repository/inspect"));
        assert!(allowed_method("task/detail"));
        assert!(allowed_method("maintenance/backup"));
        assert!(allowed_method("maintenance/restore"));
        assert!(!allowed_method("shell/execute"));
        assert!(!allowed_method("database/query"));
    }

    #[test]
    fn request_requires_the_exact_protocol_and_object_parameters() {
        let valid = UiRequest {
            protocol: PROTOCOL.to_string(),
            method: "system/status".to_string(),
            params: json!({}),
        };
        assert!(validate_ui_request(&valid).is_ok());

        let invalid = UiRequest {
            protocol: "aiao.desktop.v0".to_string(),
            method: "system/status".to_string(),
            params: json!([]),
        };
        assert!(validate_ui_request(&invalid).is_err());
    }

    #[test]
    fn only_transport_errors_restart_the_sidecar() {
        assert!(!should_restart_after(&SidecarRequestError::Application(
            "VALIDATION_ERROR: invalid task state".to_string(),
        )));
        assert!(should_restart_after(&SidecarRequestError::Transport(
            "The request channel closed.".to_string(),
        )));
    }

    #[test]
    fn packaged_sidecar_is_preferred_when_present() {
        let root = env::temp_dir().join(format!("aiao-sidecar-test-{}", std::process::id()));
        fs::create_dir_all(&root).unwrap();
        let executable = root.join("agent-orchestrator-sidecar.exe");
        fs::write(&executable, b"test").unwrap();

        let spec = SidecarSpec::discover(root.join("data"), Some(executable.clone()));

        assert_eq!(PathBuf::from(spec.program), executable);
        assert_eq!(spec.arguments.len(), 4);
        fs::remove_dir_all(root).unwrap();
    }
}
