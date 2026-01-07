/*
 Copyright 2025 Google LLC

 Licensed under the Apache License, Version 2.0 (the "License");
 you may not use this file except in compliance with the License.
 You may obtain a copy of the License at

      https://www.apache.org/licenses/LICENSE-2.0

 Unless required by applicable law or agreed to in writing, software
 distributed under the License is distributed on an "AS IS" BASIS,
 WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 See the License for the specific language governing permissions and
 limitations under the License.
 */

import { v0_8 } from "@a2ui/lit";

/**
 * Configuration interface for the Universal App Shell.
 */
export interface AppConfig {
  /** Unique key for the app (e.g., 'restaurant', 'contacts') */
  key: string;
  /** Display title of the application */
  title: string;
  /** The background for the page */
  background?: string;
  /** Path to the hero image */
  heroImage?: string;
  /** Path to the hero image */
  heroImageDark?: string;
  /** Placeholder text for the input field */
  placeholder: string;
  /** Text to display while loading (optional). Can be a single string or an array of strings to rotate. */
  loadingText?: string | string[];
  /** Optional server URL for the agent (e.g., http://localhost:10003) */
  serverUrl?: string;
  /** Theme overrides (CSS Variables) */
  theme?: v0_8.Types.Theme;
}
