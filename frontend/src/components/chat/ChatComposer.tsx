import { useEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import { AttachIcon, MicIcon, SendIcon } from "./icons";

export interface StrategyFormValues {
  situation: string;
  website_url: string;
  product: string;
  raw_message: string;
}

interface ChatComposerProps {
  onSubmit: (values: StrategyFormValues) => void;
  disabled: boolean;
  websiteFieldError?: string | null;
}

const MAX_TEXTAREA_LINES = 5;
const TEXTAREA_LINE_HEIGHT_PX = 22;

// website_url is extracted from raw_message if present; product is now
// embedded in situation text and the LLM extracts it from context -- this
// regex only ever attempts the website_url extraction, matching the
// backend's own note in SITUATION_ENRICHMENT_PROMPT that the combined
// message may carry all three pieces of information together.
function extractWebsiteUrl(text: string): string {
  const match = text.match(/\b(?:https?:\/\/\S+|www\.\S+)/i);
  if (!match) return "";
  return match[0].replace(/[),.;!?]+$/, "");
}

function domainLabel(url: string): string {
  return url.replace(/^https?:\/\//i, "").replace(/^www\./i, "").replace(/\/.*$/, "");
}

export default function ChatComposer({ onSubmit, disabled, websiteFieldError }: ChatComposerProps) {
  const [message, setMessage] = useState("");
  const [manualWebsite, setManualWebsite] = useState("");
  const [manualProduct, setManualProduct] = useState("");
  const [isContextPanelOpen, setIsContextPanelOpen] = useState(false);
  const [websiteDismissed, setWebsiteDismissed] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [isListening, setIsListening] = useState(false);
  const recognitionRef = useRef<any>(null);
  const isSpeechSupported =
    typeof window !== "undefined" &&
    !!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const maxHeight = TEXTAREA_LINE_HEIGHT_PX * MAX_TEXTAREA_LINES;
    el.style.height = `${Math.min(el.scrollHeight, maxHeight)}px`;
  }, [message]);

  const extractedWebsite = extractWebsiteUrl(message);
  const effectiveWebsite = manualWebsite.trim() || (websiteDismissed ? "" : extractedWebsite);
  const effectiveProduct = manualProduct.trim();

  function handleMessageChange(e: ChangeEvent<HTMLTextAreaElement>) {
    setMessage(e.target.value);
    setWebsiteDismissed(false);
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  function clearWebsitePill() {
    setManualWebsite("");
    setWebsiteDismissed(true);
  }

  function clearProductPill() {
    setManualProduct("");
  }

  function submit() {
    if (!message.trim() || disabled) return;
    onSubmit({
      raw_message: message.trim(),
      situation: message.trim(),
      website_url: effectiveWebsite,
      product: effectiveProduct,
    });
    setMessage("");
    setManualWebsite("");
    setManualProduct("");
    setIsContextPanelOpen(false);
    setWebsiteDismissed(false);
  }

  const startListening = () => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) return;

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = "en-IN";

    recognition.onresult = (event: any) => {
      const transcript = Array.from(event.results)
        .map((result: any) => result[0].transcript)
        .join("");
      setMessage(transcript);
    };

    recognition.onerror = (event: any) => {
      console.error("Speech error:", event.error);
      setIsListening(false);
    };

    recognition.onend = () => {
      setIsListening(false);
    };

    recognitionRef.current = recognition;
    recognition.start();
    setIsListening(true);
  };

  const stopListening = () => {
    if (recognitionRef.current) {
      recognitionRef.current.stop();
      recognitionRef.current = null;
    }
    setIsListening(false);
  };

  const toggleListening = () => {
    isListening ? stopListening() : startListening();
  };

  return (
    <div className="chat-composer">
      {(effectiveWebsite || effectiveProduct) && (
        <div className="composer-pills">
          {effectiveWebsite && (
            <span className="pill pill-website">
              {domainLabel(effectiveWebsite)}
              <button type="button" className="pill-clear" onClick={clearWebsitePill} aria-label="Clear website">
                &times;
              </button>
            </span>
          )}
          {effectiveProduct && (
            <span className="pill pill-product">
              {effectiveProduct}
              <button type="button" className="pill-clear" onClick={clearProductPill} aria-label="Clear product">
                &times;
              </button>
            </span>
          )}
        </div>
      )}

      {isContextPanelOpen && (
        <div className="composer-context-panel">
          <span className="composer-context-label">Or fill separately:</span>
          <input
            type="text"
            className="composer-context-input"
            placeholder="Company website"
            value={manualWebsite}
            onChange={(e) => setManualWebsite(e.target.value)}
          />
          <input
            type="text"
            className="composer-context-input"
            placeholder="Product name"
            value={manualProduct}
            onChange={(e) => setManualProduct(e.target.value)}
          />
        </div>
      )}
      {websiteFieldError && <p className="field-error">{websiteFieldError}</p>}

      <div className="composer-bar">
        <button
          type="button"
          className="composer-icon-button"
          title="Add website/product separately"
          aria-label="Toggle context panel"
          onClick={() => setIsContextPanelOpen((open) => !open)}
        >
          <AttachIcon />
        </button>
        <textarea
          ref={textareaRef}
          className="composer-textarea"
          rows={1}
          placeholder="Describe your situation — include the company website and product if relevant. e.g. We approached purchase at ABC Components (www.abc.com), they make gear housings..."
          value={message}
          onChange={handleMessageChange}
          onKeyDown={handleKeyDown}
        />
        {isSpeechSupported && (
          <button
            type="button"
            className={`composer-icon-button${isListening ? " listening" : ""}`}
            onClick={toggleListening}
            aria-label={isListening ? "Stop recording" : "Speak your situation"}
            title={isListening ? "Stop recording" : "Speak your situation"}
          >
            <MicIcon />
          </button>
        )}
        <button
          type="button"
          className="composer-send-button"
          disabled={disabled || !message.trim()}
          onClick={submit}
          aria-label="Send"
        >
          {disabled ? <span className="composer-send-spinner" /> : <SendIcon />}
        </button>
      </div>
    </div>
  );
}
