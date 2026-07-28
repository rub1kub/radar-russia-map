import { useEffect, useState } from "react";
import { BarChart3, X } from "lucide-react";
import { api } from "../lib/api";
import type { Analytics, SourceStat } from "../lib/api";
import { plural, severityColor, threatLabel } from "../lib/format";

type Props = {
  open: boolean;
  onClose: () => void;
};

function formatLag(seconds: number | null): string {
  if (seconds === null) return "—";
  if (seconds < 60) return `${seconds} с`;
  return `${Math.round(seconds / 60)} мин`;
}

/**
 * Аналитика источников и зон.
 *
 * Это то, чего нет ни у RadarMap, ни у Детектора АЭРО: метрики самих лент —
 * кто сообщает первым и чью информацию подтверждают остальные.
 */
export function AnalyticsPanel({ open, onClose }: Props) {
  const [sources, setSources] = useState<SourceStat[] | null>(null);
  const [zones, setZones] = useState<Analytics | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();

    Promise.all([
      api.analyticsSources(controller.signal),
      api.analyticsZones(168, controller.signal)
    ])
      .then(([sourceData, zoneData]) => {
        if (controller.signal.aborted) return;
        setSources(sourceData.sources);
        setZones(zoneData);
      })
      .catch(() => {
        if (!controller.signal.aborted) setFailed(true);
      });

    return () => controller.abort();
  }, [open]);

  if (!open) return null;

  const maxFirst = Math.max(1, ...(sources ?? []).map((item) => item.first_reports));
  const maxZone = Math.max(1, ...(zones?.top_zones ?? []).map((item) => item.events));

  return (
    // Клик мимо карточки закрывает окно — обычное поведение модального окна.
    // Проверка на сам оверлей обязательна: без неё закрывало бы и нажатие
    // внутри карточки, всплывшее до этого обработчика.
    <div
      className="analytics-overlay"
      role="dialog"
      aria-label="Аналитика"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      {/* Крестик вынесен из карточки и держится в углу экрана: карточка
          прокручивается, и вместе с ней уезжала единственная кнопка выхода. */}
      <button className="analytics-close" type="button" onClick={onClose} aria-label="Закрыть">
        <X size={18} aria-hidden="true" />
      </button>

      <div className="analytics-card">
        <div className="analytics-head">
          <BarChart3 size={17} aria-hidden="true" />
          <h2>Аналитика</h2>
        </div>

        {failed ? (
          <p className="feed-empty">Не удалось загрузить аналитику.</p>
        ) : !sources || !zones ? (
          <p className="feed-empty">Загрузка…</p>
        ) : (
          <div className="analytics-body">
            <section>
              <h3>Источники</h3>
              <p className="analytics-note">
                Кто чаще сообщает первым и как часто его сообщения подтверждают другие ленты.
              </p>
              <div className="analytics-table-wrap">
              <table className="analytics-table">
                <thead>
                  <tr>
                    <th>Канал</th>
                    <th>Первым</th>
                    <th>Подтв.</th>
                    <th>Задержка</th>
                  </tr>
                </thead>
                <tbody>
                  {sources.map((item) => (
                    <tr key={item.source_key}>
                      <td>{item.source_key}</td>
                      <td>
                        <span className="bar-cell">
                          <span
                            className="bar"
                            style={{ width: `${(item.first_reports / maxFirst) * 100}%` }}
                          />
                          <span className="bar-value">{item.first_reports}</span>
                        </span>
                      </td>
                      <td>{Math.round(item.confirmed_share * 100)}%</td>
                      <td>{formatLag(item.median_lag_sec)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            </section>

            <section>
              <h3>Где чаще всего</h3>
              <p className="analytics-note">За последнюю неделю.</p>
              <ul className="analytics-zones">
                {zones.top_zones.slice(0, 12).map((zone) => (
                  <li key={zone.zone_id}>
                    <span className="zone-name">{zone.name_ru}</span>
                    <span className="bar-cell">
                      <span
                        className="bar"
                        style={{
                          width: `${(zone.events / maxZone) * 100}%`,
                          background: severityColor(zone.max_severity, 0.8)
                        }}
                      />
                      <span className="bar-value">{zone.events}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </section>

            <section>
              <h3>Типы угроз</h3>
              <ul className="analytics-threats">
                {zones.by_threat.map((item) => (
                  <li key={item.threat_type}>
                    <span>{threatLabel(item.threat_type)}</span>
                    <strong>
                      {item.n} {plural(item.n, "событие", "события", "событий")}
                    </strong>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
