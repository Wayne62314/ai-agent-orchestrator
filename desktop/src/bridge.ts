import { invoke } from "@tauri-apps/api/core";
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
