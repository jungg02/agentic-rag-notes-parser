import { useState } from "react";

import "./ChatInput.css";

interface ChatInputProps {
  onSend: (content: string) => void;
  disabled: boolean;
}

export function ChatInput({ onSend, disabled }: ChatInputProps) {
  const [value, setValue] = useState("");

  const handleSend = () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setValue("");
  };

  return (
    <div className="chat-input">
      <input
        className="chat-input-field"
        aria-label="Chat message"
        placeholder="Ask a question about your course materials"
        value={value}
        disabled={disabled}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && handleSend()}
      />
      <button className="chat-input-send btn-primary" onClick={handleSend} disabled={disabled}>
        Send
      </button>
    </div>
  );
}
