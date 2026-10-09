// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
// [aipdd fork delta] MCP 工具清单：每行点击展开字段 schema（参数 / 类型 / 是否必填 /
// 说明）。「工具」页的内置目录与服务器「测试连接」结果共用同一渲染。
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";

/** 工具行数据（内置目录条目与测试连接结果归一后的最小形状）。 */
export interface McpToolListItemShape {
  name: string;
  description: string;
  inputSchema?: Record<string, unknown>;
}

interface SchemaField {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

/** 从 input_schema（JSON Schema）提取字段清单；缺失时返回空数组。 */
function schemaFields(schema: Record<string, unknown> | undefined): SchemaField[] {
  if (!schema) return [];
  const properties = schema.properties;
  if (!properties || typeof properties !== "object") return [];
  const required = Array.isArray(schema.required) ? schema.required.map(String) : [];
  return Object.entries(properties as Record<string, unknown>).map(([name, raw]) => {
    const prop = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
    return {
      name,
      type: typeof prop.type === "string" ? prop.type : "object",
      required: required.includes(name),
      description: typeof prop.description === "string" ? prop.description : "",
    };
  });
}

/** 字段 schema 面板：参数 / 类型 / 是否必填 / 说明。 */
export function ToolSchemaPanel({ schema }: { schema?: Record<string, unknown> }) {
  const { t } = useTranslation();
  const fields = schemaFields(schema);
  if (fields.length === 0) {
    return (
      <p className="rounded-md border border-white/[0.06] bg-white/[0.018] px-2.5 py-1.5 text-[11px] text-muted-foreground">
        {t("mcp.schema.noParams")}
      </p>
    );
  }
  return (
    <div className="overflow-hidden rounded-md border border-white/[0.06]">
      <table className="w-full text-left text-[11px]">
        <thead className="bg-white/5 text-muted-foreground">
          <tr>
            <th className="px-2.5 py-1.5 font-medium">{t("mcp.schema.colParam")}</th>
            <th className="px-2.5 py-1.5 font-medium">{t("mcp.schema.colType")}</th>
            <th className="px-2.5 py-1.5 font-medium">{t("mcp.schema.colRequired")}</th>
            <th className="px-2.5 py-1.5 font-medium">{t("mcp.schema.colDesc")}</th>
          </tr>
        </thead>
        <tbody>
          {fields.map((field) => (
            <tr key={field.name} className="border-t border-white/[0.06]">
              <td className="px-2.5 py-1.5 font-mono text-foreground">{field.name}</td>
              <td className="px-2.5 py-1.5 text-muted-foreground">{field.type}</td>
              <td className="px-2.5 py-1.5">
                {field.required ? (
                  <span className="rounded-sm bg-red-500/15 px-1.5 py-0.5 text-[10px] text-red-300">
                    {t("mcp.schema.required")}
                  </span>
                ) : (
                  <span className="text-muted-foreground/70">{t("mcp.schema.optional")}</span>
                )}
              </td>
              <td className="px-2.5 py-1.5 leading-relaxed text-muted-foreground">{field.description}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** 可展开工具清单：点击工具行展开字段 schema。 */
export function McpToolList({ tools }: { tools: readonly McpToolListItemShape[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  return (
    <div className="space-y-1.5">
      {tools.map((tool) => {
        const open = expanded === tool.name;
        return (
          <div key={tool.name} className="rounded-lg border border-white/[0.06] bg-white/[0.018]">
            <button
              type="button"
              aria-expanded={open}
              onClick={() => setExpanded(open ? null : tool.name)}
              className="flex w-full items-start gap-2 px-3 py-2 text-left"
            >
              <ChevronRight
                className={cn(
                  "mt-0.5 size-3.5 shrink-0 text-muted-foreground transition-transform",
                  open && "rotate-90",
                )}
                aria-hidden="true"
              />
              <span className="min-w-0">
                <code className="text-[12.5px] font-medium text-foreground">{tool.name}</code>
                {tool.description ? (
                  <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                    {tool.description}
                  </p>
                ) : null}
              </span>
            </button>
            {open ? (
              <div className="px-3 pb-2.5 pl-8">
                <ToolSchemaPanel schema={tool.inputSchema} />
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}