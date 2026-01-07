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

import { componentRegistry, Root } from "@a2ui/lit/ui";
import { html, css } from "lit";
import { property } from "lit/decorators.js";
// 1. Define the override
import { PremiumTextField } from "../premium-text-field.js";

// 2. Register it as "TextField"
componentRegistry.register("TextField", PremiumTextField, "premium-text-field");
console.log("Registered PremiumTextField override");

// 3. Render a standard TextField component node
const container = document.getElementById("app");
if (container) {
  const root = document.createElement("a2ui-root") as Root;

  const textFieldComponent = {
    type: "TextField",
    id: "tf-1",
    properties: {
      label: "Enter your name",
      text: "John Doe",
    },
  };

  // Root renders its *children*, so we must pass the component as a child.
  root.childComponents = [textFieldComponent];

  root.enableCustomElements = true; // Enable the feature
  container.appendChild(root);
}
