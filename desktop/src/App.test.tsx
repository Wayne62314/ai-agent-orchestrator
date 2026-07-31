import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import App from "./App";
import * as bridge from "./bridge";

const realDesktopRequest = bridge.desktopRequest;

async function openDashboard() {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", {
    name: /为长期开发任务建立/,
  });
  await user.click(screen.getByRole("button", { name: "继续" }));
  await user.click(screen.getByRole("button", { name: "继续" }));
  await user.click(screen.getByRole("button", { name: "进入工作台" }));
  await screen.findByRole("heading", {
    name: /让任务继续/,
  });
  return user;
}

describe("desktop application journeys", () => {
  it("does not overlap background initialization requests", async () => {
    let release!: (value: unknown) => void;
    const pending = new Promise<unknown>((resolve) => {
      release = resolve;
    });
    const request = vi
      .spyOn(bridge, "desktopRequest")
      .mockImplementation(async () => pending);
    try {
      render(<App />);
      await waitFor(() => expect(request).toHaveBeenCalledOnce());
      await new Promise((resolve) => window.setTimeout(resolve, 2700));
      expect(request).toHaveBeenCalledOnce();

      release(await realDesktopRequest("system/initialize"));
      await screen.findByRole("heading", {
        name: /为长期开发任务建立/,
      });
    } finally {
      request.mockRestore();
    }
  });

  it("completes first-run setup and shows a safe active task", async () => {
    await openDashboard();
    expect(screen.getByText("仅在本机运行")).toBeInTheDocument();
    expect(screen.getByText("原仓库未改动")).toBeInTheDocument();
    expect(screen.getAllByText("Codex 正在工作").length).toBeGreaterThan(0);
    expect(screen.queryByText(/^free$/i)).not.toBeInTheDocument();
  });

  it("pauses only after the mock lifecycle confirms a checkpoint", async () => {
    const user = await openDashboard();
    await user.click(screen.getByRole("button", { name: /安全暂停/ }));
    expect(await screen.findByRole("button", { name: "恢复" })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getAllByText("已安全暂停").length).toBeGreaterThan(0);
    });
  });

  it("navigates through the task wizard without a terminal", async () => {
    const user = await openDashboard();
    await user.click(screen.getAllByRole("button", { name: /新建任务/ })[0]);
    expect(screen.getByRole("heading", { name: "选择项目来源" })).toBeInTheDocument();
    expect(screen.getByLabelText("仓库路径")).toHaveValue("");
    expect(screen.getByRole("button", { name: "继续" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "浏览" }));
    await screen.findByText(/main · a1c468a3/);
    expect(screen.getByRole("button", { name: "继续" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "继续" }));
    await screen.findByRole("heading", { name: "描述清楚的目标" });
    expect(screen.getByLabelText("任务名称")).toHaveValue("");
    expect(screen.getByLabelText("目标和完成条件")).toHaveValue("");
    expect(screen.getByRole("button", { name: "继续" })).toBeDisabled();
    await user.type(screen.getByLabelText("任务名称"), "修复任务创建流程");
    await user.type(
      screen.getByLabelText("目标和完成条件"),
      "用户可以创建任务，并在失败时看到明确原因。",
    );
    await user.click(screen.getByRole("button", { name: "继续" }));
    await screen.findByRole("heading", { name: "选择权限边界" });
    await user.click(screen.getByRole("button", { name: "继续" }));
    await screen.findByRole("heading", { name: "选择验收方式" });
    expect(screen.getByText("AI 复核已开启")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /运行前端测试/ }),
    ).toBeInTheDocument();
  });

  it("uses the repository picker and shows the inspected Git revision", async () => {
    const user = await openDashboard();
    await user.click(screen.getAllByRole("button", { name: /新建任务/ })[0]);
    await user.click(screen.getByRole("button", { name: "浏览" }));
    expect(await screen.findByText(/main · a1c468a3/)).toBeInTheDocument();
    expect(screen.getByText(/检测到 2 个未提交路径/)).toBeInTheDocument();
  });

  it("creates a new local project without requiring an existing repository", async () => {
    const user = await openDashboard();
    await user.click(screen.getAllByRole("button", { name: /新建任务/ })[0]);
    await user.click(screen.getByRole("button", { name: /创建新项目/ }));
    await user.click(screen.getByRole("button", { name: "浏览" }));
    await user.type(screen.getByLabelText("项目名称"), "Clock Widget");
    expect(screen.getByRole("button", { name: "继续" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "继续" }));
    await user.type(screen.getByLabelText("任务名称"), "完成时钟小组件");
    await user.type(
      screen.getByLabelText("目标和完成条件"),
      "创建一个可以正常运行的桌面时钟。",
    );
    await user.click(screen.getByRole("button", { name: "继续" }));
    await user.click(screen.getByRole("button", { name: "继续" }));
    await user.click(screen.getByRole("button", { name: "继续" }));
    expect(
      screen.getByText("C:\\Projects\\Clock Widget"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认并创建" }));

    expect(await screen.findByText("任务尚未开始")).toBeInTheDocument();
    expect(screen.queryByText("Codex 正在处理任务")).not.toBeInTheDocument();
  });

  it("checks a manually entered repository before continuing", async () => {
    const user = await openDashboard();
    await user.click(screen.getAllByRole("button", { name: /新建任务/ })[0]);
    const repository = screen.getByLabelText("仓库路径");
    await user.type(repository, "C:\\Projects\\Real Repository");
    expect(screen.getByRole("button", { name: "继续" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "检查" }));
    expect(await screen.findByText(/main · a1c468a3/)).toBeInTheDocument();
    expect(repository).toHaveValue("C:\\Projects\\Real Repository");
    expect(screen.getByRole("button", { name: "继续" })).toBeEnabled();
  });

  it("shows task creation failures instead of silently ignoring them", async () => {
    const user = await openDashboard();
    await user.click(screen.getAllByRole("button", { name: /新建任务/ })[0]);
    await user.click(screen.getByRole("button", { name: "浏览" }));
    await screen.findByText(/main · a1c468a3/);
    await user.click(screen.getByRole("button", { name: "继续" }));
    await screen.findByRole("heading", { name: "描述清楚的目标" });
    await user.type(screen.getByLabelText("任务名称"), "验证创建失败提示");
    await user.type(
      screen.getByLabelText("目标和完成条件"),
      "创建失败时保留输入并展示可操作的错误。",
    );
    await user.click(screen.getByRole("button", { name: "继续" }));
    await screen.findByRole("heading", { name: "选择权限边界" });
    await user.click(screen.getByRole("button", { name: "继续" }));
    await screen.findByRole("heading", { name: "选择验收方式" });
    await user.click(screen.getByRole("button", { name: "继续" }));
    await screen.findByRole("heading", { name: "任务将在隔离环境中创建" });

    const request = vi
      .spyOn(bridge, "desktopRequest")
      .mockImplementation(async (method, params = {}) => {
        if (method === "task/create") {
          throw new Error("无法为这个仓库创建隔离工作区。");
        }
        return realDesktopRequest(method, params);
      });
    try {
      await user.click(screen.getByRole("button", { name: "确认并创建" }));
      expect(
        await screen.findByText("无法为这个仓库创建隔离工作区。"),
      ).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "确认并创建" })).toBeEnabled();
    } finally {
      request.mockRestore();
    }
  });

  it("loads durable task evidence for every detail tab", async () => {
    const user = await openDashboard();
    await user.click(screen.getByRole("button", { name: "当前任务" }));

    await user.click(screen.getByRole("tab", { name: "活动" }));
    expect(await screen.findByText("单元测试通过")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "运行" }));
    expect(await screen.findByText("第 2 次运行")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Checkpoint" }));
    expect(await screen.findByText("Checkpoint #1")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "验收" }));
    expect(await screen.findByText(/第 2 轮 · 单元测试/)).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "报告" }));
    expect(await screen.findByText("当前交付状态")).toBeInTheDocument();
    expect(screen.getByText("完整有效")).toBeInTheDocument();
  });

  it("backs up, exports diagnostics, and restores without a terminal", async () => {
    localStorage.setItem("aiao.notifications", "disabled");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = await openDashboard();
    await user.click(screen.getByRole("button", { name: "设置与维护" }));

    await user.click(screen.getByRole("button", { name: /数据与备份/ }));
    expect(
      await screen.findByText(/已完成并通过完整性检查/),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /日志与诊断/ }));
    expect(await screen.findByText(/diagnostics-demo.zip/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /启动与通知/ }));
    expect(await screen.findByText("Windows 本地通知已开启。")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /恢复最近备份/ }));
    expect(
      await screen.findByText(/恢复前安全副本已保留/),
    ).toBeInTheDocument();
    expect(confirm).toHaveBeenCalledOnce();
  });

  it("shows product facts without guessing a subscription plan", async () => {
    const user = await openDashboard();
    await user.click(screen.getByRole("button", { name: "设置与维护" }));

    expect(screen.getByText("AI Agent Orchestrator 0.12.0")).toBeInTheDocument();
    expect(screen.queryByText(/^free$/i)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "重新查看首次设置" }),
    ).not.toBeInTheDocument();
  });
});
