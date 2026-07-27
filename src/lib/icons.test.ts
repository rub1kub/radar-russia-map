import { describe, expect, it } from "vitest";
import { iconKindFor, isPointEvent, threatIcon } from "./icons";

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
    expect(isPointEvent("caution")).toBe(false);
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
