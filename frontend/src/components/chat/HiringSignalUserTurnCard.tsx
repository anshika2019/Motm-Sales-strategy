import type { BDHiringSignalRequest } from "../../api/types";
import CopyButton from "./CopyButton";

/** User-turn display for a hiring-signal-outreach request -- mirrors
 * BDUserTurnCard's shape but for BDHiringSignalRequest's fields (company/
 * role/website pills) instead of a free-text situation. */
export default function HiringSignalUserTurnCard({ values }: { values: BDHiringSignalRequest }) {
  const summary = [values.company_name, values.hiring_role].filter(Boolean).join(" — ") || "Hiring signal";
  const copyText = [
    values.company_name && `Company: ${values.company_name}`,
    values.company_website && `Website: ${values.company_website}`,
    values.hiring_role && `Hiring role: ${values.hiring_role}`,
    values.job_post_text && `Job post: ${values.job_post_text}`,
  ]
    .filter(Boolean)
    .join("\n");

  return (
    <div className="turn-user">
      <CopyButton text={copyText} variant="icon" tone="on-accent" />
      <div className="turn-label">Hiring signal outreach for</div>
      <p style={{ margin: 0 }}>{summary}</p>
      {(values.company_website || values.location) && (
        <div className="composer-pills" style={{ marginTop: 10 }}>
          {values.company_website && <span className="pill pill-website">{values.company_website}</span>}
          {values.location && <span className="pill pill-product">{values.location}</span>}
        </div>
      )}
    </div>
  );
}
