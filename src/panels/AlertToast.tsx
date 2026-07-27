import { AlertTriangle, X } from "lucide-react";
import type { RadarEvent } from "../lib/api";
import { severityColor, signalLabel, threatLabel } from "../lib/format";

type Props = {
  alerts: RadarEvent[];
  onDismiss: () => void;
  onPick: (event: RadarEvent) => void;
};

/**
 * Предупреждение по отслеживаемому месту.
 *
 * Всплывает только для событий, затрагивающих отмеченные пользователем зоны,
 * и только один раз на событие.
 */
export function AlertToast({ alerts, onDismiss, onPick }: Props) {
  if (!alerts.length) return null;
  const top = alerts[0];

  return (
    <div
      className="alert-toast"
      role="alert"
      style={{ borderColor: severityColor(top.severity, 0.75) }}
    >
      <span className="alert-icon" style={{ color: severityColor(top.severity, 1) }}>
        <AlertTriangle size={19} aria-hidden="true" />
      </span>

      <button className="alert-body" type="button" onClick={() => onPick(top)}>
        <span className="alert-title">{top.place_name}</span>
        <span className="alert-meta">
          {signalLabel(top.signal_type)}
          {top.threat_type !== "unknown" ? ` · ${threatLabel(top.threat_type)}` : ""}
        </span>
        {alerts.length > 1 ? (
          <span className="alert-more">и ещё {alerts.length - 1} по вашим местам</span>
        ) : (
          <span className="alert-more">по вашему месту</span>
        )}
      </button>

      <button className="alert-close" type="button" onClick={onDismiss} aria-label="Скрыть">
        <X size={15} aria-hidden="true" />
      </button>
    </div>
  );
}
