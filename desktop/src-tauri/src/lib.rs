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
    original_parent: isize,
    original_style: isize,
    original_ex_style: isize,
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
        Ok(json!({"found": false, "attached": false, "near": false, "leftButtonDown": false}))
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
        System::Threading::{
            OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
        },
        UI::{
            Input::KeyboardAndMouse::{GetAsyncKeyState, VK_LBUTTON},
            Shell::ShellExecuteW,
            WindowsAndMessaging::{
                ClientToScreen, EnumWindows, GetWindowLongPtrW, GetWindowRect,
                GetWindowTextLengthW, GetWindowTextW, GetWindowThreadProcessId, IsWindow,
                IsWindowVisible, SetParent, SetWindowLongPtrW, SetWindowPos, ShowWindow,
                GWL_EXSTYLE, GWL_STYLE, SWP_FRAMECHANGED, SWP_NOACTIVATE, SWP_SHOWWINDOW,
                SW_RESTORE, WS_CAPTION, WS_CHILD, WS_EX_APPWINDOW, WS_MAXIMIZEBOX, WS_MINIMIZEBOX,
                WS_POPUP, WS_SYSMENU, WS_THICKFRAME, WS_VISIBLE,
            },
        },
    };

    const TOP_LEVEL_MASK: isize =
        (WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU)
            as isize;

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
                position(parent as HWND, hwnd, &rect)?;
                return Ok(json!({
                    "found": true,
                    "attached": true,
                    "near": false,
                    "leftButtonDown": left_down
                }));
            }
        }
        let Some(hwnd) = find_codex_window() else {
            return Ok(json!({
                "found": false,
                "attached": false,
                "near": false,
                "leftButtonDown": left_down
            }));
        };
        let mut window_rect: RECT = unsafe { mem::zeroed() };
        if unsafe { GetWindowRect(hwnd, &mut window_rect) } == 0 {
            return Err("Windows could not read the Codex window position.".to_string());
        }
        let target = screen_rect(parent as HWND, &rect)?;
        let distance = rectangle_distance(&window_rect, &target);
        Ok(json!({
            "found": true,
            "attached": false,
            "near": distance <= 72,
            "leftButtonDown": left_down
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
        let hwnd = find_codex_window()
            .ok_or_else(|| "No visible official Codex window was found.".to_string())?;
        let style = unsafe { GetWindowLongPtrW(hwnd, GWL_STYLE) };
        let ex_style = unsafe { GetWindowLongPtrW(hwnd, GWL_EXSTYLE) };
        let original_parent = unsafe { SetParent(hwnd, parent as HWND) };
        let embedded = (style & !TOP_LEVEL_MASK) | WS_CHILD as isize | WS_VISIBLE as isize;
        unsafe {
            SetWindowLongPtrW(hwnd, GWL_STYLE, embedded);
            SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex_style & !(WS_EX_APPWINDOW as isize));
        }
        state.attached = true;
        state.window = hwnd as isize;
        state.original_parent = original_parent as isize;
        state.original_style = style;
        state.original_ex_style = ex_style;
        position(parent as HWND, hwnd, &rect)
    }

    pub fn detach(state: &mut CodexWindowState) -> Result<(), String> {
        if !state.attached {
            return Ok(());
        }
        let hwnd = state.window as HWND;
        if unsafe { IsWindow(hwnd) } != 0 {
            unsafe {
                SetParent(hwnd, state.original_parent as HWND);
                SetWindowLongPtrW(hwnd, GWL_STYLE, state.original_style);
                SetWindowLongPtrW(hwnd, GWL_EXSTYLE, state.original_ex_style);
                SetWindowPos(
                    hwnd,
                    ptr::null_mut(),
                    120,
                    80,
                    1100,
                    760,
                    SWP_FRAMECHANGED | SWP_SHOWWINDOW,
                );
                ShowWindow(hwnd, SW_RESTORE);
            }
        }
        *state = CodexWindowState::default();
        Ok(())
    }

    fn position(parent: HWND, hwnd: HWND, rect: &DockRect) -> Result<(), String> {
        let target = screen_rect(parent, rect)?;
        let width = (target.right - target.left).max(1);
        let height = (target.bottom - target.top).max(1);
        let mut origin = POINT { x: 0, y: 0 };
        if unsafe { ClientToScreen(parent, &mut origin) } == 0 {
            return Err("Windows could not locate the product window.".to_string());
        }
        let ok = unsafe {
            SetWindowPos(
                hwnd,
                ptr::null_mut(),
                target.left - origin.x,
                target.top - origin.y,
                width,
                height,
                SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED,
            )
        };
        if ok == 0 {
            return Err("Windows could not position the Codex window.".to_string());
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
        let title_len = unsafe { GetWindowTextLengthW(hwnd) };
        let mut title = vec![0u16; (title_len + 1) as usize];
        unsafe { GetWindowTextW(hwnd, title.as_mut_ptr(), title.len() as i32) };
        let title = String::from_utf16_lossy(&title).to_ascii_lowercase();
        if !title.contains("codex") {
            return false;
        }
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
        path.contains("openai.codex_") || path.contains("\\openai\\codex\\")
    }

    fn wide(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(std::iter::once(0)).collect()
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
