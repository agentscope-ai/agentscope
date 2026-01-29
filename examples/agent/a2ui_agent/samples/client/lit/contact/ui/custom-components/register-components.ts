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

import { componentRegistry } from "@a2ui/lit/ui";
import { OrgChart } from "./org-chart.js";
import { PremiumTextField } from "./premium-text-field.js";

export function registerContactComponents() {
  // Register OrgChart
  componentRegistry.register("OrgChart", OrgChart, "org-chart");

  // Register PremiumTextField as an override for TextField
  componentRegistry.register(
    "TextField",
    PremiumTextField,
    "premium-text-field"
  );

  console.log("Registered Contact App Custom Components");
}
