import "@testing-library/jest-dom";

// Polyfill scrollIntoView for jsdom (browser API not implemented in jsdom)
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function () {
    // no-op polyfill for jsdom
  };
}
