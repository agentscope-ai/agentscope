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

import { cloneDefaultTheme } from "../theme/clone-default-theme.js";
import { AppConfig } from "./types.js";
import { v0_8 } from "@a2ui/lit";

/** Elements */

const a = {
  "typography-f-sf": true,
  "typography-fs-n": true,
  "typography-w-500": true,
  "layout-as-n": true,
  "layout-dis-iflx": true,
  "layout-al-c": true,
};

const heading = {
  "typography-f-sf": true,
  "typography-fs-n": true,
  "typography-w-500": true,
  "layout-mt-0": true,
  "layout-mb-2": true,
  "color-c-n10": true,
};

const orderedList = {
  "typography-f-s": true,
  "typography-fs-n": true,
  "typography-w-400": true,
  "layout-m-0": true,
  "typography-sz-bm": true,
  "layout-as-n": true,
};

const unorderedList = {
  "typography-f-s": true,
  "typography-fs-n": true,
  "typography-w-400": true,
  "layout-m-0": true,
  "typography-sz-bm": true,
  "layout-as-n": true,
};

const listItem = {
  "typography-f-s": true,
  "typography-fs-n": true,
  "typography-w-400": true,
  "layout-m-0": true,
  "typography-sz-bm": true,
  "layout-as-n": true,
};

const theme: v0_8.Types.Theme = {
  ...cloneDefaultTheme(),
  additionalStyles: {
    Card: {
      "min-width": "320px",
      "max-width": "400px",
      margin: "0 auto",
      background:
        "linear-gradient(135deg, light-dark(#ffffff99, #ffffff44) 0%, light-dark(#ffffff, #ffffff04) 100%)",
      border: "1px solid light-dark(transparent, #ffffff35)",
      boxShadow:
        "inset 0 20px 48px light-dark(rgba(0, 0, 0, 0.02), rgba(255, 255, 255, 0.08))",
    },
    Button: {
      "--p-70": "light-dark(var(--p-60), var(--n-10))",
      "--n-60": "light-dark(var(--n-100), var(--n-0))",
    },
    Image: {
      "max-width": "120px",
      "max-height": "120px",
      marginLeft: "auto",
      marginRight: "auto",
    },
    Text: {
      "--n-40": "light-dark(var(--p-60), var(--n-90))",
    },
  },
  components: {
    AudioPlayer: {},
    Button: {
      "layout-pt-2": true,
      "layout-pb-2": true,
      "layout-pl-5": true,
      "layout-pr-5": true,
      "border-br-2": true,
      "border-bw-0": true,
      "border-bs-s": true,
      "color-bgc-p30": true,
      "color-c-n100": true,
      "behavior-ho-70": true,
    },
    Card: {
      "border-br-4": true,
      "color-bgc-p100": true,
      "layout-pt-10": true,
      "layout-pb-10": true,
      "layout-pl-4": true,
      "layout-pr-4": true,
    },
    CheckBox: {
      element: {
        "layout-m-0": true,
        "layout-mr-2": true,
        "layout-p-2": true,
        "border-br-12": true,
        "border-bw-1": true,
        "border-bs-s": true,
        "color-bgc-p100": true,
        "color-bc-p60": true,
        "color-c-n30": true,
        "color-c-p30": true,
      },
      label: {
        "color-c-p30": true,
        "typography-f-sf": true,
        "typography-v-r": true,
        "typography-w-400": true,
        "layout-flx-1": true,
        "typography-sz-ll": true,
      },
      container: {
        "layout-dsp-iflex": true,
        "layout-al-c": true,
      },
    },
    Column: {},
    DateTimeInput: {
      container: {},
      label: {},
      element: {
        "layout-pt-2": true,
        "layout-pb-2": true,
        "layout-pl-3": true,
        "layout-pr-3": true,
        "border-br-12": true,
        "border-bw-1": true,
        "border-bs-s": true,
        "color-bgc-p100": true,
        "color-bc-p60": true,
        "color-c-n30": true,
      },
    },
    Divider: {
      "color-bgc-n90": true,
      "layout-mt-6": true,
      "layout-mb-6": true,
    },
    Image: {
      all: {
        "border-br-50pc": true,
        "layout-el-cv": true,
        "layout-w-100": true,
        "layout-h-100": true,
        "layout-dsp-flexhor": true,
        "layout-al-c": true,
        "layout-sp-c": true,
        "layout-mb-3": true,
      },
      avatar: {},
      header: {},
      icon: {},
      largeFeature: {},
      mediumFeature: {},
      smallFeature: {},
    },
    Icon: {
      "border-br-1": true,
      "layout-p-2": true,
      "color-bgc-n98": true,
      "layout-dsp-flexhor": true,
      "layout-al-c": true,
      "layout-sp-c": true,
    },
    List: {
      "layout-g-4": true,
      "layout-p-2": true,
    },
    Modal: {
      backdrop: { "color-bbgc-p60_20": true },
      element: {
        "border-br-2": true,
        "color-bgc-p100": true,
        "layout-p-4": true,
        "border-bw-1": true,
        "border-bs-s": true,
        "color-bc-p80": true,
      },
    },
    MultipleChoice: {
      container: {},
      label: {},
      element: {},
    },
    Row: {
      "layout-g-4": true,
      "layout-mb-3": true,
    },
    Slider: {
      container: {},
      label: {},
      element: {},
    },
    Tabs: {
      container: {},
      controls: { all: {}, selected: {} },
      element: {},
    },
    Text: {
      all: {
        "layout-w-100": true,
        "layout-g-2": true,
        "color-c-p30": true,
      },
      h1: {
        "typography-f-sf": true,
        "typography-ta-c": true,
        "typography-v-r": true,
        "typography-w-500": true,
        "layout-mt-0": true,
        "layout-mr-0": true,
        "layout-ml-0": true,
        "layout-mb-2": true,
        "layout-p-0": true,
        "typography-sz-tl": true,
      },
      h2: {
        "typography-f-sf": true,
        "typography-ta-c": true,
        "typography-v-r": true,
        "typography-w-500": true,
        "layout-mt-0": true,
        "layout-mr-0": true,
        "layout-ml-0": true,
        "layout-mb-2": true,
        "layout-p-0": true,
        "typography-sz-tl": true,
      },
      h3: {
        "typography-f-sf": true,
        "typography-ta-c": true,
        "typography-v-r": true,
        "typography-w-500": true,
        "layout-mt-0": true,
        "layout-mr-0": true,
        "layout-ml-0": true,
        "layout-mb-0": true,
        "layout-p-0": true,
        "typography-sz-ts": true,
      },
      h4: {
        "typography-f-sf": true,
        "typography-ta-c": true,
        "typography-v-r": true,
        "typography-w-500": true,
        "layout-mt-0": true,
        "layout-mr-0": true,
        "layout-ml-0": true,
        "layout-mb-0": true,
        "layout-p-0": true,
        "typography-sz-bl": true,
      },
      h5: {
        "typography-f-sf": true,
        "typography-ta-c": true,
        "typography-v-r": true,
        "typography-w-500": true,
        "layout-mt-0": true,
        "layout-mr-0": true,
        "layout-ml-0": true,
        "layout-mb-0": true,
        "layout-p-0": true,
        "color-c-n30": true,
        "typography-sz-bm": true,
        "layout-mb-1": true,
      },
      body: {},
      caption: {},
    },
    TextField: {
      container: {
        "typography-sz-bm": true,
        "layout-w-100": true,
        "layout-g-2": true,
        "layout-dsp-flexhor": true,
        "layout-al-c": true,
      },
      label: {
        "layout-flx-0": true,
      },
      element: {
        "typography-sz-bm": true,
        "layout-pt-2": true,
        "layout-pb-2": true,
        "layout-pl-3": true,
        "layout-pr-3": true,
        "border-br-12": true,
        "border-bw-1": true,
        "border-bs-s": true,
        "color-bgc-p100": true,
        "color-bc-p60": true,
        "color-c-n30": true,
        "color-c-p30": true,
      },
    },
    Video: {
      "border-br-5": true,
      "layout-el-cv": true,
    },
  },
};

export const config: AppConfig = {
  key: "contacts",
  title: "Contact Manager",
  background: `radial-gradient(at 0% 0%, light-dark(rgba(45, 212, 191, 0.4), rgba(20, 184, 166, 0.2)) 0px, transparent 50%),
     radial-gradient(at 100% 0%, light-dark(rgba(56, 189, 248, 0.4), rgba(14, 165, 233, 0.2)) 0px, transparent 50%),
     radial-gradient(at 100% 100%, light-dark(rgba(163, 230, 53, 0.4), rgba(132, 204, 22, 0.2)) 0px, transparent 50%),
     radial-gradient(at 0% 100%, light-dark(rgba(52, 211, 153, 0.4), rgba(16, 185, 129, 0.2)) 0px, transparent 50%),
     linear-gradient(120deg, light-dark(#f0fdf4, #022c22) 0%, light-dark(#dcfce7, #064e3b) 100%)`,
  placeholder: "Alex Jordan",
  loadingText: [
    "Searching contacts...",
    "Looking up details...",
    "Verifying information...",
    "Just a moment...",
  ],
  serverUrl: "http://localhost:10003",
  theme,
};
