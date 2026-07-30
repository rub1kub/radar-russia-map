import { BarChart3, Clock, Map as MapIcon, Radio, SlidersHorizontal } from "lucide-react";

export type MobileTab = "map" | "feed" | "search" | "history" | "analytics";

type Props = {
  active: MobileTab;
  /** Сколько событий в эфире — бейдж на вкладке ленты. */
  count: number;
  onPick: (tab: MobileTab) => void;
};

/**
 * Нижняя навигация для узких экранов.
 *
 * На телефоне панели занимают почти весь экран, и открывать их плавающими
 * ярлыками по углам было негде: они наезжали друг на друга и на края.
 * Панель здесь ровно одна за раз, а таб-бар говорит, какая именно, — и
 * первым стоит «Карта», потому что уйти с панели обратно к карте нужно
 * чаще всего.
 *
 * Высота считается с safe-area: на айфонах внизу живёт системная полоса,
 * и без отступа она перекрывала бы последнюю кнопку.
 */
export function MobileTabs({ active, count, onPick }: Props) {
  const tabs: Array<{ id: MobileTab; label: string; icon: JSX.Element }> = [
    { id: "map", label: "Карта", icon: <MapIcon size={19} aria-hidden="true" /> },
    { id: "feed", label: "Эфир", icon: <Radio size={19} aria-hidden="true" /> },
    { id: "search", label: "Поиск", icon: <SlidersHorizontal size={19} aria-hidden="true" /> },
    { id: "history", label: "История", icon: <Clock size={19} aria-hidden="true" /> },
    { id: "analytics", label: "Сводка", icon: <BarChart3 size={19} aria-hidden="true" /> }
  ];

  return (
    <nav className="mobile-tabs" aria-label="Разделы">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          className={`mobile-tab ${active === tab.id ? "is-active" : ""}`}
          onClick={() => onPick(tab.id)}
          aria-current={active === tab.id ? "page" : undefined}
        >
          <span className="mobile-tab-icon">
            {tab.icon}
            {tab.id === "feed" && count > 0 ? (
              <span className="mobile-tab-badge">{count > 99 ? "99+" : count}</span>
            ) : null}
          </span>
          <span className="mobile-tab-label">{tab.label}</span>
        </button>
      ))}
    </nav>
  );
}
