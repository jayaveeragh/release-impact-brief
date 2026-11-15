import { describe, expect, it } from "vitest";
import { moduleOf, routes } from "./api";

describe("moduleOf", () => {
  const modules = ["build:module:.", "build:module:web", "build:module:web/admin"];
  it("picks the innermost module holding the file", () => {
    expect(moduleOf("web/admin/src/A.java", modules)).toBe("build:module:web/admin");
    expect(moduleOf("web/src/B.java", modules)).toBe("build:module:web");
    expect(moduleOf("src/C.java", modules)).toBe("build:module:.");
  });
  it("doesn't match a folder that only shares a prefix", () => {
    expect(moduleOf("webapp/src/D.java", ["build:module:web"])).toBeNull();
  });
});

describe("routes", () => {
  it("encodes ids with spaces and slashes into one hash segment", () => {
    expect(routes.endpoint("a__b@123", "java:endpoint:GET /x/{id}"))
      .toBe("#/view/a__b%40123/endpoint/java%3Aendpoint%3AGET%20%2Fx%2F%7Bid%7D");
  });
});
