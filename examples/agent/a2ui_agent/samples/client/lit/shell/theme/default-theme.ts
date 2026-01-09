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

/** Elements */

const a = {
  "typography-f-sf": true,
  "typography-fs-n": true,
  "typography-w-500": true,
  "layout-as-n": true,
  "layout-dis-iflx": true,
  "layout-al-c": true,
  "typography-td-none": true,
  "color-c-p40": true,
};

const audio = {
  "layout-w-100": true,
};

const body = {
  "typography-f-s": true,
  "typography-fs-n": true,
  "typography-w-400": true,
  "layout-mt-0": true,
  "layout-mb-2": true,
  "typography-sz-bm": true,
  "color-c-n10": true,
};

const button = {
  "typography-f-sf": true,
  "typography-fs-n": true,
  "typography-w-500": true,
  "layout-pt-3": true,
  "layout-pb-3": true,
  "layout-pl-5": true,
  "layout-pr-5": true,
  "layout-mb-1": true,
  "border-br-16": true,
  "border-bw-0": true,
  "border-c-n70": true,
  "border-bs-s": true,
  "color-bgc-s30": true,
  "behavior-ho-80": true,
};

const heading = {
  "typography-f-sf": true,
  "typography-fs-n": true,
  "typography-w-500": true,
  "layout-mt-0": true,
  "layout-mb-2": true,
};

const iframe = {
  "behavior-sw-n": true,
};

const input = {
  "typography-f-sf": true,
  "typography-fs-n": true,
  "typography-w-400": true,
  "layout-pl-4": true,
  "layout-pr-4": true,
  "layout-pt-2": true,
  "layout-pb-2": true,
  "border-br-6": true,
  "border-bw-1": true,
  "color-bc-s70": true,
  "border-bs-s": true,
  "layout-as-n": true,
  "color-c-n10": true,
};

const p = {
  "typography-f-s": true,
  "typography-fs-n": true,
  "typography-w-400": true,
  "layout-m-0": true,
  "typography-sz-bm": true,
  "layout-as-n": true,
  "color-c-n10": true,
};

const orderedList = {
  "typography-f-s": true,
  "typography-fs-n": true,
  "typography-w-400": true,
  "layout-m-0": true,
  "typography-sz-bm": true,
  "layout-as-n": true,
  "color-c-n10": true,
};

const unorderedList = {
  "typography-f-s": true,
  "typography-fs-n": true,
  "typography-w-400": true,
  "layout-m-0": true,
  "typography-sz-bm": true,
  "layout-as-n": true,
  "color-c-n10": true,
};

const listItem = {
  "typography-f-s": true,
  "typography-fs-n": true,
  "typography-w-400": true,
  "layout-m-0": true,
  "typography-sz-bm": true,
  "layout-as-n": true,
  "color-c-n10": true,
};

const pre = {
  "typography-f-c": true,
  "typography-fs-n": true,
  "typography-w-400": true,
  "typography-sz-bm": true,
  "typography-ws-p": true,
  "layout-as-n": true,
};

const textarea = {
  ...input,
  "layout-r-none": true,
  "layout-fs-c": true,
};

const video = {
  "layout-el-cv": true,
};

const aLight = v0_8.Styles.merge(a, {});
const inputLight = v0_8.Styles.merge(input, {});
const textareaLight = v0_8.Styles.merge(textarea, {});
const buttonLight = v0_8.Styles.merge(button, {});
const bodyLight = v0_8.Styles.merge(body, {});
const pLight = v0_8.Styles.merge(p, {});
const preLight = v0_8.Styles.merge(pre, {});
const orderedListLight = v0_8.Styles.merge(orderedList, {});
const unorderedListLight = v0_8.Styles.merge(unorderedList, {});
const listItemLight = v0_8.Styles.merge(listItem, {});

export const theme: v0_8.Types.Theme = {
  additionalStyles: {
    Button: {
      "--n-35": "var(--n-100)",
      "--n-10": "var(--n-0)",
      background:
        "linear-gradient(135deg, light-dark(#818cf8, #06b6d4) 0%, light-dark(#a78bfa, #3b82f6) 100%)",
      boxShadow: "0 4px 15px rgba(102, 126, 234, 0.4)",
      padding: "12px 28px",
      textTransform: "uppercase",
    },
    Text: {
      h1: {
        color: "transparent",
        background:
          "linear-gradient(135deg, light-dark(#818cf8, #06b6d4) 0%, light-dark(#a78bfa, #3b82f6) 100%)",
        "-webkit-background-clip": "text",
        "background-clip": "text",
        "-webkit-text-fill-color": "transparent",
      },
      h2: {
        color: "transparent",
        background:
          "linear-gradient(135deg, light-dark(#818cf8, #06b6d4) 0%, light-dark(#a78bfa, #3b82f6) 100%)",
        "-webkit-background-clip": "text",
        "background-clip": "text",
        "-webkit-text-fill-color": "transparent",
      },
      h3: {
        color: "transparent",
        background:
          "linear-gradient(135deg, light-dark(#818cf8, #06b6d4) 0%, light-dark(#a78bfa, #3b82f6) 100%)",
        "-webkit-background-clip": "text",
        "background-clip": "text",
        "-webkit-text-fill-color": "transparent",
      },
      h4: {},
      h5: {},
      body: {},
      caption: {},
    },
    Card: {
      background:
        "radial-gradient(circle at top left, light-dark(transparent, rgba(6, 182, 212, 0.15)), transparent 40%), radial-gradient(circle at bottom right, light-dark(transparent, rgba(139, 92, 246, 0.15)), transparent 40%), linear-gradient(135deg, light-dark(rgba(255, 255, 255, 0.7), rgba(30, 41, 59, 0.7)), light-dark(rgba(255, 255, 255, 0.7), rgba(15, 23, 42, 0.8)))",
    },
    TextField: {
      "--p-0": "light-dark(var(--n-0), #1e293b)",
    },
  },
  components: {
    AudioPlayer: {},
    Button: {
      "layout-pt-2": true,
      "layout-pb-2": true,
      "layout-pl-3": true,
      "layout-pr-3": true,
      "border-br-12": true,
      "border-bw-0": true,
      "border-bs-s": true,
      "color-bgc-p30": true,
      "behavior-ho-70": true,
      "typography-w-400": true,
    },
    Card: { "border-br-9": true, "layout-p-4": true, "color-bgc-n100": true },
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
    Column: {
      "layout-g-2": true,
    },
    DateTimeInput: {
      container: {
        "typography-sz-bm": true,
        "layout-w-100": true,
        "layout-g-2": true,
        "layout-dsp-flexhor": true,
        "layout-al-c": true,
        "typography-ws-nw": true,
      },
      label: {
        "color-c-p30": true,
        "typography-sz-bm": true,
      },
      element: {
        "layout-pt-2": true,
        "layout-pb-2": true,
        "layout-pl-3": true,
        "layout-pr-3": true,
        "border-br-2": true,
        "border-bw-1": true,
        "border-bs-s": true,
        "color-bgc-p100": true,
        "color-bc-p60": true,
        "color-c-n30": true,
        "color-c-p30": true,
      },
    },
    Divider: {},
    Image: {
      all: {
        "border-br-5": true,
        "layout-el-cv": true,
        "layout-w-100": true,
        "layout-h-100": true,
      },
      avatar: { "is-avatar": true },
      header: {},
      icon: {},
      largeFeature: {},
      mediumFeature: {},
      smallFeature: {},
    },
    Icon: {},
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
      },
      h1: {
        "typography-f-sf": true,
        "typography-v-r": true,
        "typography-w-400": true,
        "layout-m-0": true,
        "layout-p-0": true,
        "typography-sz-hs": true,
      },
      h2: {
        "typography-f-sf": true,
        "typography-v-r": true,
        "typography-w-400": true,
        "layout-m-0": true,
        "layout-p-0": true,
        "typography-sz-tl": true,
      },
      h3: {
        "typography-f-sf": true,
        "typography-v-r": true,
        "typography-w-400": true,
        "layout-m-0": true,
        "layout-p-0": true,
        "typography-sz-tl": true,
      },
      h4: {
        "typography-f-sf": true,
        "typography-v-r": true,
        "typography-w-400": true,
        "layout-m-0": true,
        "layout-p-0": true,
        "typography-sz-bl": true,
      },
      h5: {
        "typography-f-sf": true,
        "typography-v-r": true,
        "typography-w-400": true,
        "layout-m-0": true,
        "layout-p-0": true,
        "typography-sz-bm": true,
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
        "typography-ws-nw": true,
      },
      label: {
        "layout-flx-0": true,
        "color-c-p30": true,
      },
      element: {
        "typography-sz-bm": true,
        "layout-pt-2": true,
        "layout-pb-2": true,
        "layout-pl-3": true,
        "layout-pr-3": true,
        "border-br-2": true,
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
  elements: {
    a: aLight,
    audio,
    body: bodyLight,
    button: buttonLight,
    h1: heading,
    h2: heading,
    h3: heading,
    h4: heading,
    h5: heading,
    iframe,
    input: inputLight,
    p: pLight,
    pre: preLight,
    textarea: textareaLight,
    video,
  },
  markdown: {
    p: [...Object.keys(pLight)],
    h1: [...Object.keys(heading)],
    h2: [...Object.keys(heading)],
    h3: [...Object.keys(heading)],
    h4: [...Object.keys(heading)],
    h5: [...Object.keys(heading)],
    ul: [...Object.keys(unorderedListLight)],
    ol: [...Object.keys(orderedListLight)],
    li: [...Object.keys(listItemLight)],
    a: [...Object.keys(aLight)],
    strong: [],
    em: [],
  },
};
