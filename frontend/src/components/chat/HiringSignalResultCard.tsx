import ReactMarkdown from "react-markdown";
import type { BDHiringSignalResponse } from "../../api/types";
import CopyButton from "./CopyButton";

type HiringSignalResult = Omit<BDHiringSignalResponse, "id" | "sources">;

interface HiringSignalResultCardProps {
  result: HiringSignalResult;
}

/** Plain-text version of the whole card, for the top-level CopyButton --
 * mirrors every section rendered below so copying matches what's on
 * screen (same convention as messageAsPlainText() in
 * HistoryMessageCard.tsx). */
function resultAsPlainText(result: HiringSignalResult): string {
  const {
    company_understanding: cu,
    commercial_interpretation: ci,
    motm_fit: mf,
    whatsapp_messages: wm,
    response_handling: rh,
  } = result;
  return [
    "COMPANY UNDERSTANDING",
    `What they do: ${cu.products_services}`,
    `Industries/applications: ${cu.industries_applications}`,
    `Typical buyers: ${cu.typical_buyers}`,
    `Business type: ${cu.business_type}`,
    "",
    "COMMERCIAL INTERPRETATION",
    `Why hiring: ${ci.why_hiring}`,
    `Business objective: ${ci.business_objective}`,
    `Expansion opportunity: ${ci.expansion_opportunity}`,
    "",
    "MOTM FIT",
    `Positioning: ${mf.positioning}`,
    `Relevant capabilities: ${mf.relevant_capabilities.join(", ")}`,
    `Key differentiators: ${mf.key_differentiators.join(", ")}`,
    mf.why_relevant,
    "",
    "WHATSAPP SEQUENCE",
    `Message 1: ${wm.message_1}`,
    "",
    `Message 2: ${wm.message_2}`,
    "",
    `Message 3: ${wm.message_3}`,
    "",
    "IF THEY REPLY...",
    `"Send details": ${rh.send_details}`,
    `"What exactly do you do?": ${rh.what_do_you_do}`,
    `"We're already hiring someone": ${rh.already_hiring}`,
    `"Not interested": ${rh.not_interested}`,
  ].join("\n");
}

/** Renders the Hiring-Signal Outreach Agent's fixed multi-part output --
 * stage 1's analysis report (markdown), stage 2's company understanding /
 * MOTM fit / 3 WhatsApp messages (as chat-style bubbles) / 4 canned
 * replies. Deliberately not routed through HistoryMessageCard's narrative/
 * strategy rendering -- this shape doesn't fit either. */
export default function HiringSignalResultCard({ result }: HiringSignalResultCardProps) {
  const {
    signal_analysis,
    company_understanding,
    commercial_interpretation,
    motm_fit,
    whatsapp_messages,
    response_handling,
  } = result;

  return (
    <div className="turn-assistant">
      <CopyButton text={resultAsPlainText(result)} variant="icon" />

      <details>
        <summary style={{ cursor: "pointer", color: "var(--text-muted)", fontSize: 13 }}>
          Hiring signal analysis
        </summary>
        <div className="narrative-markdown" style={{ marginTop: 8 }}>
          <ReactMarkdown>{signal_analysis}</ReactMarkdown>
        </div>
      </details>

      <div className="section">
        <h4>Company understanding</h4>
        <p>
          <strong>What they do:</strong> {company_understanding.products_services}
        </p>
        <p>
          <strong>Industries/applications:</strong> {company_understanding.industries_applications}
        </p>
        <p>
          <strong>Typical buyers:</strong> {company_understanding.typical_buyers}
        </p>
        <p>
          <strong>Business type:</strong> {company_understanding.business_type}
        </p>
      </div>

      <div className="section">
        <h4>Commercial interpretation</h4>
        <p>
          <strong>Why hiring:</strong> {commercial_interpretation.why_hiring}
        </p>
        <p>
          <strong>Business objective:</strong> {commercial_interpretation.business_objective}
        </p>
        <p>
          <strong>Expansion opportunity:</strong> {commercial_interpretation.expansion_opportunity}
        </p>
      </div>

      <div className="section">
        <h4>MOTM fit</h4>
        <p>
          <strong>Positioning:</strong> {motm_fit.positioning}
        </p>
        <p>
          <strong>Relevant capabilities:</strong> {motm_fit.relevant_capabilities.join(", ")}
        </p>
        <p>
          <strong>Key differentiators:</strong> {motm_fit.key_differentiators.join(", ")}
        </p>
        <p>{motm_fit.why_relevant}</p>
      </div>

      <div className="section">
        <h4>WhatsApp sequence</h4>
        {[whatsapp_messages.message_1, whatsapp_messages.message_2, whatsapp_messages.message_3].map(
          (message, i) => (
            <div key={i} className="turn-assistant" style={{ marginBottom: 8 }}>
              <CopyButton text={message} variant="icon" />
              <div className="turn-label">Message {i + 1}</div>
              <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{message}</p>
            </div>
          ),
        )}
      </div>

      <div className="section">
        <h4>If they reply...</h4>
        <p>
          <strong>"Send details":</strong> {response_handling.send_details}
        </p>
        <p>
          <strong>"What exactly do you do?":</strong> {response_handling.what_do_you_do}
        </p>
        <p>
          <strong>"We're already hiring someone":</strong> {response_handling.already_hiring}
        </p>
        <p>
          <strong>"Not interested":</strong> {response_handling.not_interested}
        </p>
      </div>
    </div>
  );
}
