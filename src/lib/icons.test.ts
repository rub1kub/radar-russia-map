import { describe, expect, it } from "vitest";
import {
  directionArrow,
  iconFreshness,
  iconKindFor,
  iconVisible,
  isPointEvent,
  threatIcon
} from "./icons";
import { ZONE_FADE_FLOOR } from "./paint";

describe("iconFreshness", () => {
  const MIN = 60 * 1000;

  it("свежий значок в полную силу", () => {
    expect(iconFreshness(0)).toBe(1);
  });

  it("двадцать минут заметно ярче двух часов", () => {
    // Ровно та жалоба, с которой правило переписано: прежний срок в 30
    // минут с полом 0.2 упирался в пол уже к двадцати минутам, и разница
    // возрастов не читалась.
    const twenty = iconFreshness(20 * MIN);
    const twoHours = iconFreshness(120 * MIN);
    expect(twenty).toBeGreaterThan(twoHours * 1.8);
    expect(twoHours).toBe(ZONE_FADE_FLOOR);
  });

  it("ракета гаснет быстрее дрона", () => {
    expect(iconFreshness(10 * MIN, "rocket")).toBeLessThan(iconFreshness(10 * MIN, "uav"));
  });

  it("после окна пролёта значок не ставится вовсе", () => {
    // Борт улетел — значок «здесь» был бы враньём. Двухчасовые метки
    // висели на карте только потому, что событие ещё не закрыто.
    expect(iconVisible(20 * MIN, "uav")).toBe(true);
    expect(iconVisible(40 * MIN, "uav")).toBe(false);
    expect(iconVisible(120 * MIN, "uav")).toBe(false);
    // Ракета покидает район быстрее: окно короче, минимум 8 минут.
    expect(iconVisible(10 * MIN, "rocket")).toBe(false);
  });
});

describe("iconKindFor", () => {
  it("исход события важнее типа угрозы", () => {
    // Сбитие БПЛА — это про перехват, а не про то, чем летели.
    expect(iconKindFor("intercept", "uav")).toBe("intercept");
    expect(iconKindFor("impact", "rocket")).toBe("impact");
    expect(iconKindFor("allclear", "uav")).toBe("allclear");
  });

  it("для фиксации берёт тип угрозы", () => {
    expect(iconKindFor("detection", "uav")).toBe("uav");
    expect(iconKindFor("detection", "fpv")).toBe("fpv");
    expect(iconKindFor("detection", "rocket")).toBe("rocket");
    expect(iconKindFor("detection", "kab")).toBe("kab");
    expect(iconKindFor("detection", "bek")).toBe("bek");
    expect(iconKindFor("detection", "aviation")).toBe("aviation");
  });

  it("неизвестную угрозу не теряет", () => {
    expect(iconKindFor("detection", "unknown")).toBe("unknown");
    expect(iconKindFor("detection", "нечто")).toBe("unknown");
  });
});

describe("isPointEvent", () => {
  it("точечные сигналы получают значок", () => {
    expect(isPointEvent("detection")).toBe(true);
    expect(isPointEvent("intercept")).toBe(true);
    expect(isPointEvent("impact")).toBe(true);
  });

  it("площадные сигналы рисуются заливкой, а не значком", () => {
    expect(isPointEvent("danger")).toBe(false);
    expect(isPointEvent("alarm")).toBe(false);
  });
});

describe("threatIcon", () => {
  it("возвращает валидный data URI", () => {
    const uri = threatIcon("uav", "rgba(246, 199, 61, 1)");
    expect(uri.startsWith("data:image/svg+xml;charset=utf-8,")).toBe(true);
    expect(uri).not.toContain("#");
    expect(uri).not.toContain("<");
  });

  it("декодируется обратно в корректный SVG", () => {
    const svg = decodeURIComponent(
      threatIcon("rocket", "rgba(233, 62, 78, 1)").split(",").slice(1).join(",")
    );
    expect(svg).toContain("<svg");
    expect(svg).toContain("</svg>");
    expect(svg).toContain("rgba(233, 62, 78, 1)");
  });

  it("прозрачность попадает в разметку", () => {
    const svg = decodeURIComponent(threatIcon("uav", "red", 0.4).split(",").slice(1).join(","));
    expect(svg).toContain('opacity="0.40"');
  });

  it("неизвестный вид не роняет генерацию", () => {
    expect(() => threatIcon("нет-такого" as never, "red")).not.toThrow();
  });

  it("каждый вид даёт свою картинку", () => {
    const kinds = ["uav", "fpv", "rocket", "kab", "bek", "aviation", "intercept", "impact", "allclear", "unknown"] as const;
    const uris = new Set(kinds.map((kind) => threatIcon(kind, "red")));
    expect(uris.size).toBe(kinds.length);
  });
});

describe("directionArrow", () => {
  it("возвращает валидный data URI с цветом уровня", () => {
    const uri = directionArrow("rgba(233, 62, 78, 1)");
    expect(uri.startsWith("data:image/svg+xml;charset=utf-8,")).toBe(true);
    const svg = decodeURIComponent(uri.split(",").slice(1).join(","));
    expect(svg).toContain("<svg");
    expect(svg).toContain("rgba(233, 62, 78, 1)");
  });

  it("прозрачность попадает в разметку", () => {
    const svg = decodeURIComponent(directionArrow("red", 0.4).split(",").slice(1).join(","));
    expect(svg).toContain('opacity="0.40"');
  });
});

describe("значок дальнобойного дрона", () => {
  it("у БПЛА и FPV разные глифы", () => {
    expect(threatIcon("uav", "#fff")).not.toBe(threatIcon("fpv", "#fff"));
  });

  it("БПЛА рисуется самолётной схемой, а не мультикоптером", () => {
    // Вглубь страны идут аппараты самолётной схемы с дальностью в сотни
    // километров; мультикоптер обещал бы совсем другую угрозу.
    const uav = decodeURIComponent(threatIcon("uav", "#fff"));
    const fpv = decodeURIComponent(threatIcon("fpv", "#fff"));
    expect(uav).not.toContain("circle cx=\"11\"");
    expect(fpv).toContain("circle cx=\"11\"");
  });
});

describe("значок значит «здесь»", () => {
  it("оповещение по целой области значка не получает", () => {
    // Центр области — точка случайная: она попадает в тихий район, человек
    // нажимает именно туда и получает «сообщений нет». Треть значков на
    // карте стояла так. Область показывает заливка.
    expect(isPointEvent("detection", "region")).toBe(false);
    expect(isPointEvent("intercept", "region")).toBe(false);
  });

  it("район и населённый пункт значок получают", () => {
    expect(isPointEvent("detection", "district")).toBe(true);
    expect(isPointEvent("intercept", "place")).toBe(true);
  });

  it("площадные сигналы значка не получают нигде", () => {
    expect(isPointEvent("danger", "place")).toBe(false);
    expect(isPointEvent("alarm", "district")).toBe(false);
  });
});
