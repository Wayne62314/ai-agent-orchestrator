import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";
import { openUrl } from "@tauri-apps/plugin-opener";
import {
  isPermissionGranted,
  requestPermission,
  sendNotification,
} from "@tauri-apps/plugin-notification";
import { mockRequest } from "./mockTransport";

const isTauri = () => "__TAURI_INTERNALS__" in window;

export async function desktopRequest<T>(
  method: string,
  params: Record<string, unknown> = {},
): Promise<T> {
  if (!isTauri()) return mockRequest<T>(method, params);
  return invoke<T>("sidecar_request", {
    request: {
      protocol: "aiao.desktop.v1",
      method,
      params,
    },
  });
}

export async function chooseRepositoryFolder(): Promise<string | null> {
  if (!isTauri()) return "C:\\Projects\\Northstar";
  const selected = await open({
    directory: true,
    multiple: false,
    title: "选择 Git 仓库",
  });
  return typeof selected === "string" ? selected : null;
}

export async function chooseProjectParentFolder(): Promise<string | null> {
  if (!isTauri()) return "C:\\Projects";
  const selected = await open({
    directory: true,
    multiple: false,
    title: "选择新项目的保存位置",
  });
  return typeof selected === "string" ? selected : null;
}

export async function openTrustedLoginUrl(url: string): Promise<void> {
  const parsed = new URL(url);
  if (parsed.protocol !== "https:") {
    throw new Error("登录地址必须使用 HTTPS。");
  }
  if (!isTauri()) return;
  await openUrl(url);
}

export async function sendLocalNotification(
  title: string,
  body: string,
): Promise<boolean> {
  if (!isTauri()) return true;
  let granted = await isPermissionGranted();
  if (!granted) {
    granted = (await requestPermission()) === "granted";
  }
  if (!granted) return false;
  sendNotification({ title, body });
  return true;
}

export interface CodexDockState {
  found: boolean;
  attached: boolean;
  near: boolean;
  leftButtonDown: boolean;
}

export interface DockRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export async function openCodexThread(threadId: string): Promise<void> {
  if (!isTauri()) return;
  await invoke("open_codex_thread", { threadId });
}

export async function pollCodexDock(rect: DockRect): Promise<CodexDockState> {
  if (!isTauri()) {
    return { found: true, attached: false, near: false, leftButtonDown: false };
  }
  return invoke<CodexDockState>("codex_dock_poll", { rect });
}

export async function attachCodexWindow(rect: DockRect): Promise<void> {
  if (!isTauri()) return;
  await invoke("attach_codex_window", { rect });
}

export async function detachCodexWindow(): Promise<void> {
  if (!isTauri()) return;
  await invoke("detach_codex_window");
}
