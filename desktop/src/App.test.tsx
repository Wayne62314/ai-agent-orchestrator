import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "./App";
import * as bridge from "./bridge";

const realDesktopRequest = bridge.desktopRequest;

async function openWorkspace() {
  localStorage.clear();
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", { name: /为长期开发任务建立/ });
  await user.click(screen.getByRole("button", { name: "继续" }));
  await user.click(screen.getByRole("button", { name: "继续" }));
  await user.click(screen.getByRole("button", { name: "进入工作台" }));
  await screen.findByText("官方 Codex");
  return user;
}

describe("Agent Dock golden journey", () => {
  it("does not overlap background initialization requests", async () => {
    let release!: (value: unknown) => void;
    const pending = new Promise<unknown>((resolve) => { release = resolve; });
    const request = vi.spyOn(bridge, "desktopRequest").mockImplementation(async () => pending);
    try {
      render(<App />);
      await waitFor(() => expect(request).toHaveBeenCalledOnce());
      await new Promise((resolve) => window.setTimeout(resolve, 2700));
      expect(request).toHaveBeenCalledOnce();
      release(await realDesktopRequest("system/initialize"));
      await screen.findByRole("heading", { name: /为长期开发任务建立/ });
    } finally {
      request.mockRestore();
    }
  });

  it("shows real tasks, the Codex socket, and the message queue", async () => {
    await openWorkspace();
    expect(screen.getByRole("complementary", { name: "任务导航" })).toBeInTheDocument();
    expect(screen.getByText("把官方 Codex 窗口拖到这里")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "消息队列" })).toBeInTheDocument();
    expect(screen.getByText("仅在本机运行")).toBeInTheDocument();
    expect(screen.queryByText(/^free$/i)).not.toBeInTheDocument();
  });

  it("switches product tasks and requests the matching Codex thread", async () => {
    const request = vi.spyOn(bridge, "desktopRequest");
    try {
      const user = await openWorkspace();
      await user.click(screen.getByRole("button", { name: /整理搜索结果缓存/ }));
      expect(await screen.findByRole("heading", { name: "整理搜索结果缓存" })).toBeInTheDocument();
      await waitFor(() => expect(request).toHaveBeenCalledWith(
        "task/codex-thread",
        expect.objectContaining({ taskId: expect.any(String) }),
      ));
    } finally {
      request.mockRestore();
    }
  });

  it("creates a new local project without an existing repository", async () => {
    const user = await openWorkspace();
    await user.click(screen.getAllByRole("button", { name: /新建任务/ })[0]);
    await user.click(screen.getByRole("button", { name: /创建新项目/ }));
    await user.click(screen.getByRole("button", { name: "浏览" }));
    await user.type(screen.getByLabelText("项目名称"), "Clock Widget");
    await user.click(screen.getByRole("button", { name: "继续" }));
    await user.type(screen.getByLabelText("任务名称"), "完成时钟小组件");
    await user.type(screen.getByLabelText("目标和完成条件"), "创建一个可以正常运行的桌面时钟。");
    await user.click(screen.getByRole("button", { name: "继续" }));
    await user.click(screen.getByRole("button", { name: "继续" }));
    await user.click(screen.getByRole("button", { name: "继续" }));
    expect(screen.getByText("C:\\Projects\\Clock Widget")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认并创建" }));
    expect(await screen.findByRole("heading", { name: "完成时钟小组件" })).toBeInTheDocument();
  });

  it("keeps optional project checks optional", async () => {
    const user = await openWorkspace();
    await user.click(screen.getAllByRole("button", { name: /新建任务/ })[0]);
    await user.click(screen.getByRole("button", { name: "浏览" }));
    await screen.findByText(/main · a1c468a3/);
    await user.click(screen.getByRole("button", { name: "继续" }));
    await user.type(screen.getByLabelText("任务名称"), "无脚本任务");
    await user.type(screen.getByLabelText("目标和完成条件"), "由 AI 根据目标复核结果。");
    await user.click(screen.getByRole("button", { name: "继续" }));
    await user.click(screen.getByRole("button", { name: "继续" }));
    expect(screen.getByText("AI 复核已开启")).toBeInTheDocument();
  });

  it("shows creation failures instead of silently ignoring them", async () => {
    const user = await openWorkspace();
    await user.click(screen.getAllByRole("button", { name: /新建任务/ })[0]);
    await user.click(screen.getByRole("button", { name: "浏览" }));
    await screen.findByText(/main · a1c468a3/);
    await user.click(screen.getByRole("button", { name: "继续" }));
    await user.type(screen.getByLabelText("任务名称"), "失败提示");
    await user.type(screen.getByLabelText("目标和完成条件"), "失败时展示原因。");
    await user.click(screen.getByRole("button", { name: "继续" }));
    await user.click(screen.getByRole("button", { name: "继续" }));
    await user.click(screen.getByRole("button", { name: "继续" }));
    const request = vi.spyOn(bridge, "desktopRequest").mockImplementation(async (method, params = {}) => {
      if (method === "task/create") throw new Error("无法为这个仓库创建隔离工作区。");
      return realDesktopRequest(method, params);
    });
    try {
      await user.click(screen.getByRole("button", { name: "确认并创建" }));
      expect(await screen.findByText("无法为这个仓库创建隔离工作区。")).toBeInTheDocument();
    } finally {
      request.mockRestore();
    }
  });

  it("keeps maintenance available without guessing a subscription plan", async () => {
    const user = await openWorkspace();
    await user.click(screen.getByRole("button", { name: "设置" }));
    expect(screen.getByText("AI Agent Orchestrator 0.12.2")).toBeInTheDocument();
    expect(screen.queryByText(/^free$/i)).not.toBeInTheDocument();
  });
});
