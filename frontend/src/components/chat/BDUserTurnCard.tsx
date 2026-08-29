import type { BDStrategyFormValues } from "./BDComposer";
import CopyButton from "./CopyButton";

// BD counterpart of UserTurnCard.tsx -- shows prospect company/website
// pills instead of website/product, since BD's optional context is "who
// MOTM is selling to", not "what's being sold" (there is no product field).
export default function BDUserTurnCard({ values }: { values: BDStrategyFormValues }) {
  const text = values.raw_message || values.situation;
  return (
    <div className="turn-user">
      <CopyButton text={text} variant="icon" tone="on-accent" />
      <div className="turn-label">You asked</div>
      <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{text}</p>
      {(values.prospect_company || values.prospect_website) && (
        <div className="composer-pills" style={{ marginTop: 10 }}>
          {values.prospect_company && <span className="pill pill-website">{values.prospect_company}</span>}
          {values.prospect_website && <span className="pill pill-product">{values.prospect_website}</span>}
        </div>
      )}
    </div>
  );
}
