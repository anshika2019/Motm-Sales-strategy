import { useState, type FormEvent } from "react";
import type { BDHiringSignalRequest } from "../../api/types";

interface HiringSignalComposerProps {
  onSubmit: (values: BDHiringSignalRequest) => void;
  disabled: boolean;
  onCancel: () => void;
}

// Composer for the Hiring-Signal Outreach Agent -- a fixed field set
// (company/job-post/hiring-role/etc.) rather than a free-text situation,
// matching BDHiringSignalRequest. Per the backend, at least one of
// company_name/company_website/job_post_text is required; the rest are
// optional ("use available information intelligently" -- see the router's
// rejection message for an empty request). Grouped into labeled sections
// below purely for visual breathing room -- same fields, same submit
// payload, same validation as before.
export default function HiringSignalComposer({ onSubmit, disabled, onCancel }: HiringSignalComposerProps) {
  const [companyName, setCompanyName] = useState("");
  const [companyWebsite, setCompanyWebsite] = useState("");
  const [hiringRole, setHiringRole] = useState("");
  const [location, setLocation] = useState("");
  const [contactDetails, setContactDetails] = useState("");
  const [senderName, setSenderName] = useState("");
  const [jobPostText, setJobPostText] = useState("");
  const [notes, setNotes] = useState("");

  const hasUsableInput = companyName.trim() || companyWebsite.trim() || jobPostText.trim();

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!hasUsableInput || disabled) return;
    onSubmit({
      company_name: companyName.trim() || undefined,
      company_website: companyWebsite.trim() || undefined,
      hiring_role: hiringRole.trim() || undefined,
      location: location.trim() || undefined,
      contact_details: contactDetails.trim() || undefined,
      sender_name: senderName.trim() || undefined,
      job_post_text: jobPostText.trim() || undefined,
      notes: notes.trim() || undefined,
    });
    setCompanyName("");
    setCompanyWebsite("");
    setHiringRole("");
    setLocation("");
    setContactDetails("");
    setSenderName("");
    setJobPostText("");
    setNotes("");
  }

  return (
    <form className="chat-composer" onSubmit={handleSubmit}>
      <div className="hiring-form-header">
        <h3 className="hiring-form-title">Approach a company that's hiring</h3>
        <p className="hiring-form-subtitle">
          Turn a hiring signal into an outreach message. Company name, website, or the job post is
          required — everything else is optional.
        </p>
      </div>

      <div className="hiring-form-section">
        <div className="hiring-form-section-label">Company details</div>
        <div className="hiring-form-grid">
          <input
            type="text"
            className="composer-context-input"
            placeholder="Company name"
            value={companyName}
            onChange={(e) => setCompanyName(e.target.value)}
          />
          <input
            type="text"
            className="composer-context-input"
            placeholder="Company website"
            value={companyWebsite}
            onChange={(e) => setCompanyWebsite(e.target.value)}
          />
        </div>
      </div>

      <div className="hiring-form-section">
        <div className="hiring-form-section-label">Hiring details</div>
        <div className="hiring-form-grid">
          <input
            type="text"
            className="composer-context-input"
            placeholder="Hiring role (e.g. Sales Engineer)"
            value={hiringRole}
            onChange={(e) => setHiringRole(e.target.value)}
          />
          <input
            type="text"
            className="composer-context-input"
            placeholder="Location"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
          />
        </div>
      </div>

      <div className="hiring-form-section">
        <div className="hiring-form-section-label">Contact details</div>
        <div className="hiring-form-grid">
          <input
            type="text"
            className="composer-context-input"
            placeholder="Contact details (if known)"
            value={contactDetails}
            onChange={(e) => setContactDetails(e.target.value)}
          />
          <input
            type="text"
            className="composer-context-input"
            placeholder="Your name (to sign messages as)"
            value={senderName}
            onChange={(e) => setSenderName(e.target.value)}
          />
        </div>
      </div>

      <div className="hiring-form-section">
        <div className="hiring-form-section-label">Job post / LinkedIn</div>
        <textarea
          className="composer-context-input"
          style={{ resize: "vertical" }}
          placeholder="Paste the job post / LinkedIn text here (optional but helps a lot)"
          value={jobPostText}
          onChange={(e) => setJobPostText(e.target.value)}
          rows={4}
        />
      </div>

      <div className="hiring-form-section">
        <div className="hiring-form-section-label">Additional notes</div>
        <input
          type="text"
          className="composer-context-input"
          placeholder="Additional notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
      </div>

      <div className="composer-form-actions">
        <button type="button" className="link-button" onClick={onCancel} disabled={disabled}>
          Cancel
        </button>
        <button type="submit" className="primary-button" style={{ width: "auto" }} disabled={disabled || !hasUsableInput}>
          {disabled ? "Generating…" : "Generate outreach"}
        </button>
      </div>
    </form>
  );
}
