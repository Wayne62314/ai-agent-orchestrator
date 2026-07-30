import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import App from "./App";

async function openDashboard() {
  const user = userEvent.setup();
  render(<App />);
  await screen.findByRole("heading", {
    name: /让 Codex 长时间工作/,
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
  it("completes first-run setup and shows a safe active task", async () => {
    await openDashboard();
    expect(screen.getByText("仅在本机运行")).toBeInTheDocument();
    expect(screen.getByText("原仓库未改动")).toBeInTheDocument();
    expect(screen.getAllByText("Codex 正在工作").length).toBeGreaterThan(0);
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
    expect(screen.getByRole("heading", { name: "选择 Git 仓库" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "继续" }));
    expect(screen.getByRole("heading", { name: "描述清楚的目标" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "继续" }));
    expect(screen.getByRole("heading", { name: "选择权限边界" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "继续" }));
    expect(screen.getByRole("heading", { name: "定义自动验收" })).toBeInTheDocument();
  });

  it("uses the repository picker and shows the inspected Git revision", async () => {
    const user = await openDashboard();
    await user.click(screen.getAllByRole("button", { name: /新建任务/ })[0]);
    await user.click(screen.getByRole("button", { name: "浏览" }));
    expect(await screen.findByText(/main · a1c468a3/)).toBeInTheDocument();
    expect(screen.getByText(/检测到 2 个未提交路径/)).toBeInTheDocument();
  });
});
