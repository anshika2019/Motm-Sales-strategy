import { useEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import { MicIcon, SendIcon } from "./icons";

// BD's turn shape -- mirrors StrategyFormValues (ChatComposer.tsx) but with
// prospect_company/prospect_website/contact_designation/opportunity_stage/
// additional_context instead of website_url/product, matching
// BDStrategyRequest in api/types.ts. Nothing here is required except
// situation -- BD sells MOTM itself, so there's no mandatory
// website/product pair to scrape (see BDStrategyRequest's docstring in
// app/models/schemas.py).
export interface BDStrategyFormValues {
  situation: string;
  raw_message: string;
  prospect_company: string;
  prospect_website: string;
  contact_designation: string;
  opportunity_stage: string;
  additional_context: string;
}

interface BDComposerProps {
  onSubmit: (values: BDStrategyFormValues) => void;
  disabled: boolean;
  // Plain-chat mode (BDChatPage's default, unselected composer state) reuses
  // this same component for its textarea/mic/send behavior but hides the
  // optional prospect-details panel -- default true keeps existing "Describe
  // a situation" tab behavior unchanged.
  showDetailsPanel?: boolean;
}

const MAX_TEXTAREA_LINES = 5;
const TEXTAREA_LINE_HEIGHT_PX = 22;

function domainLabel(url: string): string {
  return url.replace(/^https?:\/\//i, "").replace(/^www\./i, "").replace(/\/.*$/, "");
}

export default function BDComposer({ onSubmit, disabled, showDetailsPanel = true }: BDComposerProps) {
  const [message, setMessage] = useState("");
  const [prospectCompany, setProspectCompany] = useState("");
  const [prospectWebsite, setProspectWebsite] = useState("");
  const [contactDesignation, setContactDesignation] = useState("");
  const [opportunityStage, setOpportunityStage] = useState("");
  const [additionalContext, setAdditionalContext] = useState("");
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

  function handleMessageChange(e: ChangeEvent<HTMLTextAreaElement>) {
    setMessage(e.target.value);
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  function clearCompanyPill() {
    setProspectCompany("");
    setProspectWebsite("");
  }

  function submit() {
    if (!message.trim() || disabled) return;
    onSubmit({
      raw_message: message.trim(),
      situation: message.trim(),
      prospect_company: prospectCompany.trim(),
      prospect_website: prospectWebsite.trim(),
      contact_designation: contactDesignation.trim(),
      opportunity_stage: opportunityStage.trim(),
      additional_context: additionalContext.trim(),
    });
    setMessage("");
    setProspectCompany("");
    setProspectWebsite("");
    setContactDesignation("");
    setOpportunityStage("");
    setAdditionalContext("");
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

  const hasProspectPill = prospectCompany.trim() || prospectWebsite.trim();

  return (
    <div className="chat-composer">
      {showDetailsPanel && hasProspectPill && (
        <div className="composer-pills">
          <span className="pill pill-website">
            {prospectCompany.trim() || domainLabel(prospectWebsite.trim())}
            <button type="button" className="pill-clear" onClick={clearCompanyPill} aria-label="Clear prospect">
              &times;
            </button>
          </span>
        </div>
      )}

      {showDetailsPanel && (
        <div className="composer-context-panel">
          <span className="composer-context-label">Optional prospect details:</span>
          <input
            type="text"
            className="composer-context-input"
            placeholder="Prospect company"
            value={prospectCompany}
            onChange={(e) => setProspectCompany(e.target.value)}
          />
          <input
            type="text"
            className="composer-context-input"
            placeholder="Prospect website"
            value={prospectWebsite}
            onChange={(e) => setProspectWebsite(e.target.value)}
          />
          <input
            type="text"
            className="composer-context-input"
            placeholder="Contact designation (e.g. MD, Purchase Head)"
            value={contactDesignation}
            onChange={(e) => setContactDesignation(e.target.value)}
          />
          <input
            type="text"
            className="composer-context-input"
            placeholder="Opportunity stage"
            value={opportunityStage}
            onChange={(e) => setOpportunityStage(e.target.value)}
          />
          <input
            type="text"
            className="composer-context-input"
            placeholder="Additional context"
            value={additionalContext}
            onChange={(e) => setAdditionalContext(e.target.value)}
          />
        </div>
      )}

      <div className="composer-bar">
        <textarea
          ref={textareaRef}
          className="composer-textarea"
          rows={1}
          placeholder="What is happening? e.g. We met the MD of a 150 crore company, he already has five salespeople and isn't sure why he needs MOTM..."
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
