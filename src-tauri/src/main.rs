#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod request_limits;

use std::{
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{atomic::{AtomicBool, Ordering}, Arc, Mutex},
    time::Duration,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{Manager, RunEvent};
use tauri_plugin_dialog::DialogExt;
use tokio::sync::watch;

#[derive(Clone)]
struct Connection { port: u16, token: String }

struct Backend {
    ready: watch::Sender<Option<Result<Connection, String>>>,
    child: Mutex<Option<Child>>,
    closing: AtomicBool,
    client: reqwest::Client,
    data_dir: PathBuf,
    #[cfg(windows)]
    job: Mutex<Option<WindowsJob>>,
}

#[derive(Deserialize)]
struct Ready { event: String, port: u16, token: String }

#[derive(Serialize)]
struct ApiResponse { status: u16, data: Value }

impl Backend {
    async fn connection(&self) -> Result<Connection, String> {
        let mut receiver = self.ready.subscribe();
        tokio::time::timeout(Duration::from_secs(60), async {
            loop {
                if let Some(result) = receiver.borrow().clone() { return result; }
                receiver.changed().await.map_err(|_| "Desktop engine stopped".to_string())?;
            }
        }).await.map_err(|_| "Engine startup timed out. Restart Swarm Workbench; see engine.log in the application data folder.".to_string())?
    }

    fn start(self: &Arc<Self>) -> Result<(), String> {
        let executable = std::env::current_exe().map_err(|e| e.to_string())?;
        let binary = executable.parent().ok_or("Missing application directory")?
            .join(if cfg!(windows) { "swarm-engine.exe" } else { "swarm-engine" });
        let mut command = Command::new(&binary);
        command.args(["--data-dir"]).arg(&self.data_dir)
            .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000); // CREATE_NO_WINDOW: no extra console window.
        }
        let mut child = command.spawn().map_err(|e| format!("Cannot start the bundled engine: {e}"))?;
        #[cfg(windows)]
        match WindowsJob::attach(&child) {
            Ok(job) => *self.job.lock().unwrap() = Some(job),
            Err(error) => { let _ = child.kill(); let _ = child.wait(); return Err(error); }
        }
        let stdout = child.stdout.take().ok_or("Missing engine readiness pipe")?;
        let stderr = child.stderr.take().ok_or("Missing engine diagnostics pipe")?;
        *self.child.lock().unwrap() = Some(child);
        let state = Arc::clone(self);
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if let Ok(ready) = serde_json::from_str::<Ready>(&line) {
                    if ready.event == "ready" && ready.port > 0 && ready.token.len() >= 32 {
                        state.ready.send_replace(Some(Ok(Connection { port: ready.port, token: ready.token })));
                    }
                }
            }
            if !state.closing.load(Ordering::SeqCst) {
                state.ready.send_replace(Some(Err("The bundled engine stopped. Restart Swarm Workbench; diagnostics are in engine.log / desktop.log in the application data folder.".into())));
            }
        });
        let log = self.data_dir.join("desktop.log");
        std::thread::spawn(move || {
            if let Ok(mut file) = std::fs::OpenOptions::new().create(true).append(true).open(log) {
                for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                    let _ = writeln!(file, "{line}");
                }
            }
        });
        Ok(())
    }

    fn stop(&self) {
        if let Some(mut child) = self.child.lock().unwrap().take() {
            if let Some(mut input) = child.stdin.take() { let _ = input.write_all(b"shutdown\n"); }
            for _ in 0..70 {
                if matches!(child.try_wait(), Ok(Some(_))) { return; }
                std::thread::sleep(Duration::from_millis(100));
            }
            let _ = child.kill();
            let _ = child.wait();
        }
        #[cfg(windows)]
        self.job.lock().unwrap().take();
    }
}

// Job ownership closes every engine descendant if the desktop crashes or is forcibly closed.
#[cfg(windows)]
struct WindowsJob(usize);
#[cfg(windows)]
impl WindowsJob {
    fn attach(child: &Child) -> Result<Self, String> {
        use std::os::windows::io::AsRawHandle;
        use windows_sys::Win32::System::JobObjects::*;
        unsafe {
            let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if handle.is_null() { return Err(format!("Cannot own engine processes: {}", std::io::Error::last_os_error())); }
            let job = Self(handle as usize);
            let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(handle, JobObjectExtendedLimitInformation,
                &info as *const _ as *const _, std::mem::size_of_val(&info) as u32) == 0
                || AssignProcessToJobObject(handle, child.as_raw_handle() as _) == 0 {
                return Err(format!("Cannot contain engine processes: {}", std::io::Error::last_os_error()));
            }
            Ok(job)
        }
    }
}
#[cfg(windows)]
impl Drop for WindowsJob {
    fn drop(&mut self) { unsafe { windows_sys::Win32::Foundation::CloseHandle(self.0 as _); } }
}

#[tauri::command]
async fn backend_ready(state: tauri::State<'_, Arc<Backend>>) -> Result<(), String> {
    state.connection().await.map(|_| ())
}

#[tauri::command]
async fn backend_request(path: String, method: String, body: Option<Value>, state: tauri::State<'_, Arc<Backend>>) -> Result<ApiResponse, String> {
    if !path.starts_with("/api/") || path.contains(['\\', '?', '#', '%']) || path.contains("..")
        || path == "/api/session" || !matches!(method.as_str(), "GET" | "POST" | "PUT" | "PATCH" | "DELETE") {
        return Err("Invalid desktop API request".into());
    }
    if !request_limits::accepts_body(&path, &method, body.as_ref().map(|v| v.to_string().len()).unwrap_or(0)) { return Err("Request too large".into()); }
    let connection = state.connection().await?;
    let url = format!("http://127.0.0.1:{}{}", connection.port, path);
    let mut request = state.client.request(method.parse().map_err(|_| "Invalid method")?, url).bearer_auth(&connection.token);
    if let Some(body) = body { request = request.json(&body); }
    let response = request.send().await.map_err(|_| "Cannot reach the bundled engine. Restart Swarm Workbench.".to_string())?;
    let status = response.status().as_u16();
    let data = response.json().await.map_err(|_| "Invalid engine response".to_string())?;
    Ok(ApiResponse { status, data })
}

#[tauri::command]
async fn export_json(app: tauri::AppHandle, name: String, value: Value) -> Result<bool, String> {
    let text = serde_json::to_string_pretty(&value).map_err(|e| e.to_string())?;
    if text.len() > 20_000_000 { return Err("Export too large".into()); }
    let filename = name.chars().filter(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.')).collect::<String>();
    tauri::async_runtime::spawn_blocking(move || {
        let chosen = app.dialog().file().set_title("Export from Swarm Workbench")
            .set_file_name(if filename.is_empty() { "swarm-export.json" } else { &filename })
            .add_filter("JSON", &["json"]).blocking_save_file();
        if let Some(chosen) = chosen {
            let path = chosen.into_path().map_err(|e| e.to_string())?;
            std::fs::write(path, text).map_err(|e| e.to_string())?;
            Ok(true)
        } else { Ok(false) }
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn choose_tls_file(app: tauri::AppHandle, private_key: bool) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog().file().set_title(if private_key { "Choose TLS private key" } else { "Choose TLS certificate" })
            .add_filter("TLS files", &["pem", "crt", "cer", "key"])
            .blocking_pick_file().map(|p| p.into_path().map(|p| p.to_string_lossy().into_owned()).map_err(|e| e.to_string())).transpose()
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
fn open_app_data(app: tauri::AppHandle, state: tauri::State<'_, Arc<Backend>>) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    app.opener().open_path(state.data_dir.to_string_lossy(), None::<&str>).map_err(|e| e.to_string())
}

#[tauri::command]
async fn choose_project_folder(app: tauri::AppHandle) -> Result<Option<String>, String> {
    tauri::async_runtime::spawn_blocking(move || {
        app.dialog().file().set_title("Choose a project for your agents").blocking_pick_folder()
            .map(|p| p.into_path().map(|p| p.to_string_lossy().into_owned()).map_err(|e| e.to_string())).transpose()
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn choose_local_file(app: tauri::AppHandle, kind: String) -> Result<Option<String>, String> {
    if !matches!(kind.as_str(), "gguf" | "runtime" | "backup") { return Err("Unknown file type".into()); }
    tauri::async_runtime::spawn_blocking(move || {
        let mut dialog = app.dialog().file().set_title("Choose a local Swarm resource");
        if kind == "gguf" { dialog = dialog.add_filter("GGUF model", &["gguf"]); }
        if kind == "backup" { dialog = dialog.add_filter("Encrypted Swarm backup", &["swarmbak"]); }
        dialog.blocking_pick_file().map(|p| p.into_path().map(|p| p.to_string_lossy().into_owned()).map_err(|e| e.to_string())).transpose()
    }).await.map_err(|e| e.to_string())?
}

#[tauri::command]
async fn export_backup(app: tauri::AppHandle, state: tauri::State<'_, Arc<Backend>>, name: String) -> Result<bool, String> {
    if name.contains(['/', '\\', ':']) || !name.starts_with("swarm-full-") || !name.ends_with(".swarmbak") { return Err("Invalid backup name".into()); }
    let folder = state.data_dir.join("backups").canonicalize().map_err(|_| "Backup folder is unavailable")?;
    let source = folder.join(&name).canonicalize().map_err(|_| "Backup file is unavailable")?;
    if !source.starts_with(&folder) || !source.is_file() { return Err("Invalid backup path".into()); }
    tauri::async_runtime::spawn_blocking(move || {
        let chosen = app.dialog().file().set_title("Save encrypted Swarm backup").set_file_name(&name)
            .add_filter("Encrypted backup", &["swarmbak"]).blocking_save_file();
        if let Some(chosen) = chosen {
            std::fs::copy(source, chosen.into_path().map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
            Ok(true)
        } else { Ok(false) }
    }).await.map_err(|e| e.to_string())?
}

fn configured_updater(app: &tauri::AppHandle, endpoint: String, public_key: String) -> Result<tauri_plugin_updater::Updater, String> {
    use tauri_plugin_updater::UpdaterExt;
    let url: reqwest::Url = endpoint.parse().map_err(|_| "Enter an HTTPS update manifest")?;
    if url.scheme() != "https" || !url.username().is_empty() || url.password().is_some() || public_key.trim().is_empty() {
        return Err("Updates require HTTPS and a trusted public signing key".into());
    }
    app.updater_builder().pubkey(public_key).endpoints(vec![url]).map_err(|e| e.to_string())?
        .build().map_err(|e| e.to_string())
}

#[tauri::command]
async fn check_update(app: tauri::AppHandle, endpoint: String, public_key: String) -> Result<Option<Value>, String> {
    match configured_updater(&app, endpoint, public_key)?.check().await.map_err(|e| e.to_string())? {
        Some(update) => Ok(Some(serde_json::json!({"version": update.version, "notes": update.body.unwrap_or_default()}))),
        None => Ok(None),
    }
}

#[tauri::command]
async fn install_update(app: tauri::AppHandle, endpoint: String, public_key: String) -> Result<(), String> {
    let update = configured_updater(&app, endpoint, public_key)?.check().await.map_err(|e| e.to_string())?
        .ok_or("No newer release is available")?;
    update.download_and_install(|_, _| {}, || {}).await.map_err(|e| e.to_string())?;
    app.restart();
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize(); let _ = window.show(); let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            let args = std::env::args_os().collect::<Vec<_>>();
            let chosen_data_dir = args.windows(2).find(|pair| pair[0] == "--data-dir").map(|pair| PathBuf::from(&pair[1]));
            let data_dir = chosen_data_dir.or_else(|| std::env::var_os("SWARM_DATA_DIR").map(PathBuf::from))
                .unwrap_or(app.path().app_local_data_dir()?);
            std::fs::create_dir_all(&data_dir)?;
            let (ready, _) = watch::channel(None);
            let state = Arc::new(Backend {
                ready, child: Mutex::new(None), closing: AtomicBool::new(false), data_dir,
                client: reqwest::Client::builder().no_proxy().connect_timeout(Duration::from_secs(3))
                    .timeout(Duration::from_secs(180)).build()?,
                #[cfg(windows)] job: Mutex::new(None),
            });
            if let Err(error) = state.start() { state.ready.send_replace(Some(Err(error))); }
            app.manage(state);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_ready, backend_request, export_json, choose_tls_file, open_app_data, choose_project_folder, choose_local_file, export_backup, check_update, install_update])
        .build(tauri::generate_context!()).expect("Cannot start Swarm Workbench");
    app.run(|app, event| {
        if let RunEvent::ExitRequested { api, .. } = event {
            let state = app.state::<Arc<Backend>>();
            if !state.closing.swap(true, Ordering::SeqCst) {
                api.prevent_exit();
                let state = Arc::clone(&state);
                let app = app.clone();
                std::thread::spawn(move || { state.stop(); app.exit(0); });
            }
        }
    });
}
