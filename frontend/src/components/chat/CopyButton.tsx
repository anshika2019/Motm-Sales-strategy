import { useState } from "react";
import { CheckIcon, CopyIcon } from "./icons";

interface CopyButtonProps {
  text: string;
  /** "full" (default) renders the existing labeled button used in the
   * message-actions row. "icon" renders a compact icon-only button meant
   * for the top-right corner of a message card, matching the small
   * hover-style copy control Claude/ChatGPT show near the top of a
   * response. */
  variant?: "full" | "icon";
  /** "on-accent" is for placement on the accent-colored user bubble
   * (turn-user), where the default muted-on-surface styling wouldn't have
   * enough contrast. */
  tone?: "default" | "on-accent";
}

export default function CopyButton({ text, variant = "full", tone = "default" }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      // Clipboard API can fail (permissions, insecure context) -- fall
      // back to a hidden textarea + execCommand so copying still works.
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  if (variant === "icon") {
    return (
      <button
        className={`copy-btn-icon${tone === "on-accent" ? " on-accent" : ""}${copied ? " copied" : ""}`}
        onClick={handleCopy}
        title="Copy to clipboard"
        aria-label="Copy to clipboard"
        type="button"
      >
        {copied ? <CheckIcon /> : <CopyIcon />}
      </button>
    );
  }

  return (
    <button className="copy-btn" onClick={handleCopy} title="Copy to clipboard" type="button">
      {copied ? <CheckIcon /> : <CopyIcon />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
}
