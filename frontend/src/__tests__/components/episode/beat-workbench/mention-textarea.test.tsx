// SPDX-License-Identifier: Elastic-2.0
// Copyright (c) 2026 ClaymoreLab
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { useState } from "react";

import { MentionTextarea } from "@/components/episode/beat-workbench/mention-textarea";

function ControlledMentionTextarea() {
  const [value, setValue] = useState("");
  return (
    <MentionTextarea
      aria-label="画面描述"
      value={value}
      onChange={(event) => setValue(event.target.value)}
      mentionLabels={["陆辰_青年时期", "羊皮笔记本"]}
    />
  );
}

describe("MentionTextarea", () => {
  it("opens candidates after @ and inserts the selected mention", () => {
    render(<ControlledMentionTextarea />);

    const textarea = screen.getByRole("textbox", { name: "画面描述" });
    fireEvent.change(textarea, {
      target: { value: "@", selectionStart: 1, selectionEnd: 1 },
    });

    const option = screen.getByRole("option", { name: "陆辰_青年时期" });
    fireEvent.mouseDown(option);

    expect(screen.getByDisplayValue("@陆辰_青年时期")).toBeInTheDocument();
  });

  it("confirms the highlighted candidate when pressing space", () => {
    render(<ControlledMentionTextarea />);

    const textarea = screen.getByRole("textbox", {
      name: "画面描述",
    }) as HTMLTextAreaElement;
    fireEvent.change(textarea, {
      target: { value: "@", selectionStart: 1, selectionEnd: 1 },
    });
    expect(
      screen.getByRole("option", { name: "陆辰_青年时期" }),
    ).toBeInTheDocument();

    fireEvent.keyDown(textarea, { key: " " });

    // Space confirms the first candidate and leaves a trailing separator.
    expect(textarea.value).toBe("@陆辰_青年时期 ");
  });

  it("adds a separator when typing after a complete mention", () => {
    render(<ControlledMentionTextarea />);

    const textarea = screen.getByRole("textbox", {
      name: "画面描述",
    }) as HTMLTextAreaElement;
    fireEvent.change(textarea, {
      target: {
        value: "@陆辰_青年时期走近",
        selectionStart: 9,
        selectionEnd: 9,
      },
    });

    expect(textarea.value).toBe("@陆辰_青年时期 走近");
  });

  it("does not hijack space while an IME is composing", () => {
    render(<ControlledMentionTextarea />);

    const textarea = screen.getByRole("textbox", {
      name: "画面描述",
    }) as HTMLTextAreaElement;
    fireEvent.change(textarea, {
      target: { value: "@", selectionStart: 1, selectionEnd: 1 },
    });

    fireEvent.keyDown(textarea, { key: " ", isComposing: true });

    // The picker is left untouched so the input method keeps the space.
    expect(textarea.value).toBe("@");
  });
});
