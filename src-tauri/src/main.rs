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
                let _ = child.kill();
            }
        }
    });
}
