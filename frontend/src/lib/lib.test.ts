import { describe, expect, it } from "vitest";
import { NA, fmtDuration, fmtInt, fmtMs, fmtPct, fmtUsd } from "./format";
import { addEdge, autoMapping, newNode, removeNodes, toSubmittable, uniqueId } from "./workflow-edit";
import type { Agent, IR } from "./types";

describe("formatting never invents numbers", () => {
  it("renders missing values as Not available", () => {
    for (const f of [fmtUsd, fmtInt, fmtPct, fmtMs, fmtDuration]) {
      expect(f(null)).toBe(NA);
      expect(f(undefined)).toBe(NA);
    }
  });
  it("formats real values", () => {
    expect(fmtUsd(0)).toBe("$0.00");
    expect(fmtUsd(0.0002)).toBe("$0.0002");
    expect(fmtPct(0.756)).toBe("76%");
    expect(fmtMs(250)).toBe("250 ms");
    expect(fmtDuration(125)).toBe("2m 05s");
  });
});

const agent = (id: string, input: object, output: object): Agent => ({
  id, name: id, role: "", description: "", provider: "nebius", model: null, tools: ["RepositoryRead"], permissions: ["repository.read"],
  inputSchema: input, outputSchema: output, timeoutS: 60, builtin: true, systemContract: "",
  routing: { complexity: "low", risk: "low", latency: "normal", verificationCritical: false },
});

describe("workflow editing", () => {
  const plan = { type: "object", properties: { stack: { type: "object" } } };
  const planner = agent("planner", { type: "object" }, plan);
  const scanner = agent("scanner", { type: "object", properties: { plan, stack: { type: "object" } } }, { type: "object" });

  it("generates unique, valid ids", () => {
    expect(uniqueId("Repository Scanner!", [])).toBe("repository_scanner");
    expect(uniqueId("a", ["a", "a_2"])).toBe("a_3");
    expect(uniqueId("9lives", [])).toMatch(/^[a-z]/);
  });

  it("auto-maps same-name fields and whole-schema handoffs", () => {
    const a = newNode("AGENT", [], planner);
    const b = newNode("AGENT", [a.id], scanner);
    expect(autoMapping(a, b)).toEqual(expect.arrayContaining([{ from: "$.stack", to: "stack" }, { from: "$", to: "plan" }]));
  });

  it("connects, labels condition branches, and removes nodes with references", () => {
    const c = newNode("CONDITION", []);
    const t = newNode("AGENT", [c.id], planner);
    const f = newNode("AGENT", [c.id, t.id], planner);
    const j = { ...newNode("JOIN", [c.id, t.id, f.id]), config: { required: [t.id, f.id] } };
    let ir: IR = { name: "x", goal: "g", nodes: [c, t, f, j], edges: [] };
    ir = addEdge(ir, c.id, t.id);
    ir = addEdge(ir, c.id, f.id);
    ir = addEdge(ir, c.id, t.id); // duplicate ignored
    expect(ir.edges.map((e) => e.condition)).toEqual(["true", "false"]);
    ir = addEdge(ir, t.id, j.id);
    ir = removeNodes(ir, [t.id]);
    expect(ir.edges.some((e) => e.source === t.id || e.target === t.id)).toBe(false);
    expect(ir.nodes.find((n) => n.id === j.id)!.config!.required).toEqual([f.id]);
  });

  it("strips server-owned fields before submitting a version", () => {
    const ir = { id: "w", projectId: "p", version: 3, createdBy: "u", createdAt: "t", name: "x", goal: "g", nodes: [], edges: [] } as IR;
    expect(toSubmittable(ir)).toEqual({ name: "x", goal: "g", nodes: [], edges: [] });
  });
});
