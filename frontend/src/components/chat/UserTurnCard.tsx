import type { StrategyFormValues } from "./ChatComposer";
import CopyButton from "./CopyButton";

export default function UserTurnCard({ values }: { values: StrategyFormValues }) {
  const text = values.raw_message || values.situation;
  return (
    <div className="turn-user">
      <CopyButton text={text} variant="icon" tone="on-accent" />
      <div className="turn-label">You asked</div>
      <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{text}</p>
      {(values.website_url || values.product) && (
        <div className="composer-pills" style={{ marginTop: 10 }}>
          {values.website_url && <span className="pill pill-website">{values.website_url}</span>}
          {values.product && <span className="pill pill-product">{values.product}</span>}
        </div>
      )}
    </div>
  );
}
