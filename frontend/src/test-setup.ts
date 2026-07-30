import { vi } from "vitest";
import "@testing-library/jest-dom";

// Mock scrollIntoView for all tests
Element.prototype.scrollIntoView = vi.fn();
