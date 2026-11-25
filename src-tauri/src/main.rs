// GrepLens desktop shell.
// Starts the bundled Python back end (a sidecar) on 127.0.0.1, waits until it answers,
// then opens the interface it serves. The back end is stopped when the window closes.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::io::{Read, Write};
use std::net::TcpStream;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, Url};
use tauri_plugin_shell::{process::CommandChild, ShellExt};

const PORT: u16 = 8710;

struct Backend(Mutex<Option<CommandChild>>);

fn backend_ready() -> bool {
    let Ok(mut stream) = TcpStream::connect_timeout(&([127, 0, 0, 1], PORT).into(), Duration::from_millis(500)) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    if stream.write_all(b"GET /api/health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n").is_err() {
        return false;
    }
    let mut reply = String::new();
    let _ = stream.read_to_string(&mut reply);
    reply.starts_with("HTTP/1.1 200") || reply.starts_with("HTTP/1.0 200")
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let resources = app.path().resource_dir()?;
            // Reuse a back end that is already running (for example a second window); otherwise start one.
            let child = if backend_ready() {
                None
            } else {
                let (_rx, child) = app
                    .shell()
                    .sidecar("greplens-backend")?
                    .env("GREPLENS_RESOURCES", resources.to_string_lossy().to_string())
                    .env("GREPLENS_PORT", PORT.to_string())
                    .spawn()?;
                Some(child)
            };
            app.manage(Backend(Mutex::new(child)));

            let window = app.get_webview_window("main").expect("main window");
            set_title_bar_logo(&window);
            std::thread::spawn(move || {
                let start = Instant::now();
                while !backend_ready() && start.elapsed() < Duration::from_secs(120) {
                    std::thread::sleep(Duration::from_millis(300));
                }
                let url = Url::parse(&format!("http://127.0.0.1:{PORT}/")).expect("valid url");
                let _ = window.navigate(url);
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building GrepLens");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(child) = handle.state::<Backend>().0.lock().unwrap().take() {
                stop_tree(child.pid());
                let _ = child.kill();
            }
        }
    });
}

/// The title bar shows the in-app logo (no background); the desktop shortcut and the taskbar keep
/// the app icon (logo on the gradient circle). Only the small window icon is replaced.
#[cfg(windows)]
fn set_title_bar_logo(window: &tauri::WebviewWindow) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        CreateIconFromResourceEx, GetSystemMetrics, SendMessageW, ICON_SMALL, LR_DEFAULTCOLOR, SM_CXSMICON, SM_CYSMICON,
        WM_SETICON,
    };
    const LOGO: &[u8] = include_bytes!("../icons/window-logo.png");
    let Ok(hwnd) = window.hwnd() else { return };
    unsafe {
        let icon = CreateIconFromResourceEx(LOGO.as_ptr(), LOGO.len() as u32, 1, 0x0003_0000,
            GetSystemMetrics(SM_CXSMICON), GetSystemMetrics(SM_CYSMICON), LR_DEFAULTCOLOR);
        if !icon.is_null() {
            SendMessageW(hwnd.0 as _, WM_SETICON, ICON_SMALL as usize, icon as isize);
        }
    }
}

#[cfg(not(windows))]
fn set_title_bar_logo(_window: &tauri::WebviewWindow) {}

/// Stop the back end together with the AI engine processes it started.
#[cfg(windows)]
fn stop_tree(pid: u32) {
    use std::os::windows::process::CommandExt;
    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let _ = std::process::Command::new("taskkill")
        .args(["/PID", &pid.to_string(), "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .status();
}

#[cfg(not(windows))]
fn stop_tree(pid: u32) {
    let _ = std::process::Command::new("pkill").args(["-TERM", "-P", &pid.to_string()]).status();
}
