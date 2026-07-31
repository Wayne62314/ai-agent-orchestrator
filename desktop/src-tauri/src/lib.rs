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
}

impl DesktopState {
    fn new(spec: SidecarSpec) -> Self {
        Self {
            manager: Arc::new(Mutex::new(SidecarManager::new(spec))),
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
        if result.is_err() {
            if let Some(mut process) = self.process.take() {
                process.stop();
            }
        }
        result
    }
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

    fn request(&mut self, request_id: u64, encoded: &[u8]) -> Result<Value, String> {
        self.stdin
            .write_all(encoded)
            .and_then(|_| self.stdin.write_all(b"\n"))
            .and_then(|_| self.stdin.flush())
            .map_err(|_| "The sidecar request channel closed unexpectedly.".to_string())?;
        let raw = self
            .responses
            .recv_timeout(RESPONSE_TIMEOUT)
            .map_err(|_| "The local sidecar did not respond within 30 seconds.".to_string())?;
        if raw.len() > MAX_MESSAGE_BYTES {
            return Err("The sidecar response is larger than 1 MiB.".to_string());
        }
        let response: Value = serde_json::from_str(&raw)
            .map_err(|_| "The sidecar returned an invalid response.".to_string())?;
        if response.get("protocol").and_then(Value::as_str) != Some(PROTOCOL)
            || response.get("id").and_then(Value::as_u64) != Some(request_id)
        {
            return Err("The sidecar response did not match the request.".to_string());
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
            return Err(format!("{code}: {message}"));
        }
        response
            .get("result")
            .cloned()
            .ok_or_else(|| "The sidecar response has no result.".to_string())
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
        .invoke_handler(tauri::generate_handler![sidecar_request])
        .run(tauri::generate_context!())
        .expect("failed to run AI Agent Orchestrator");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn command_allowlist_rejects_arbitrary_shell_or_database_methods() {
        assert!(allowed_method("task/pause"));
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
