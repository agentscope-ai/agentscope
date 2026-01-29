# A2UI Theming & Configuration Guide

This guide explains how the Universal App Shell handles theming and how to add new sample applications seamlessly.

## Architecture Overview

The styling system is built on two distinct layers:

### 1. **Base Layer (`default-theme.ts`)**

- **Role**: Structural & Functional Styles.
- **What it does**: Maps A2UI components (like `Text`, `Card`, `Row`) to functional CSS utility classes (e.g., `layout-w-100`, `typography-f-sf`).
- **When to touch**: Rarely. Only if you need to change the fundamental layout behavior of a component across all shell apps.

### 2. **Configuration Layer (`configs/*.ts`)**

- **Role**: App Identity & Brand Overrides.
- **What it does**: Allows for app-level theme overrides.
- **Key Mechanism**: The `AppConfig` interface allows you to provide a new theme by setting items in the `theme` property.
- **When to touch**: Whenever you add a new app and want to change an app's theme from the default theme provided with the shell.

---

## How to Add a New Sample App

Follow these steps to add a new application (e.g., "Flight Booker") with its own unique theme.

### Step 1: Create the Config

Create a new file `configs/flights.ts`:

```typescript
import { AppConfig } from "./types.js";
import { cloneDefaultTheme } from "../theme/clone-default-theme.js";

const theme = cloneDefaultTheme();
// Set your variables, e.g., theme.components.Card = { 'color-bgc-n100': true }

export const config: AppConfig = {
  key: "flights",
  title: "Flight Booker",
  heroImage: "/hero-flights.png",
  heroImageDark: "/hero-flights-dark.png", // Optional
  placeholder: "Where do you want to go?",
  loadingText: ["Checking availability...", "Finding best rates..."],
  serverUrl: "http://localhost:10004", // Your agent's URL
  theme, // Apply the theme.
};
```

### Step 2: Register the Config

Update `app.ts` to include your new config:

```typescript
import { config as flightsConfig } from "./configs/flights.js";

const configs: Record<string, AppConfig> = {
  restaurant: restaurantConfig,
  contacts: contactsConfig,
  flights: flightsConfig, // Add this line
};
```

### Step 3: Run It

Access your new app by adding the `app` query parameter:
`http://localhost:5173/?app=flights`

The App Shell will automatically:

1.  Load your `flights` config.
2.  Apply your theme to the A2UI root's theme context.
3.  Connect to your specified `serverUrl`.

---

## Reference: Styling Levers

This section lists the available styling "levers" (utility classes) you can use in your `theme.ts` file or directly in your components. These are defined in the core library (`renderers/lit/src/0.8/styles`).

### 1. Layout (`layout-`)

**Source:** `styles/layout.ts`

| Category        | Prefix        | Scale/Values                                | Examples                                                    |
| :-------------- | :------------ | :------------------------------------------ | :---------------------------------------------------------- |
| **Padding**     | `layout-p-`   | 0-24 (1 = 4px)                              | `layout-p-4` (16px), `layout-pt-2` (Top 8px), `layout-px-4` |
| **Margin**      | `layout-m-`   | 0-24 (1 = 4px)                              | `layout-m-0`, `layout-mb-4` (Bottom 16px), `layout-mx-auto` |
| **Gap**         | `layout-g-`   | 0-24 (1 = 4px)                              | `layout-g-2` (8px), `layout-g-4` (16px)                     |
| **Width**       | `layout-w-`   | 10-100 (Percentage)                         | `layout-w-100` (100%), `layout-w-50` (50%)                  |
| **Width (Px)**  | `layout-wp-`  | 0-15 (1 = 4px)                              | `layout-wp-10` (40px)                                       |
| **Height**      | `layout-h-`   | 10-100 (Percentage)                         | `layout-h-100` (100%)                                       |
| **Height (Px)** | `layout-hp-`  | 0-15 (1 = 4px)                              | `layout-hp-10` (40px)                                       |
| **Display**     | `layout-dsp-` | `none`, `block`, `grid`, `flex`, `iflex`    | `layout-dsp-flexhor` (Row), `layout-dsp-flexvert` (Col)     |
| **Alignment**   | `layout-al-`  | `fs` (Start), `fe` (End), `c` (Center)      | `layout-al-c` (Align Items Center)                          |
| **Justify**     | `layout-sp-`  | `c` (Center), `bt` (Between), `ev` (Evenly) | `layout-sp-bt` (Justify Content Space Between)              |
| **Flex**        | `layout-flx-` | `0` (None), `1` (Grow)                      | `layout-flx-1` (Flex Grow 1)                                |
| **Position**    | `layout-pos-` | `a` (Absolute), `rel` (Relative)            | `layout-pos-rel`                                            |

### 2. Colors (`color-`)

**Source:** `styles/colors.ts`

| Category         | Prefix       | Scale/Values        | Examples                                                              |
| :--------------- | :----------- | :------------------ | :-------------------------------------------------------------------- |
| **Text Color**   | `color-c-`   | Palette Key + Shade | `color-c-p50` (Primary), `color-c-n10` (Black), `color-c-e40` (Error) |
| **Background**   | `color-bgc-` | Palette Key + Shade | `color-bgc-p100` (White/Lightest), `color-bgc-s30` (Secondary Dark)   |
| **Border Color** | `color-bc-`  | Palette Key + Shade | `color-bc-p60` (Primary Border)                                       |

**Palette Keys:**

- `p` = Primary (Brand)
- `s` = Secondary
- `t` = Tertiary
- `n` = Neutral (Grays)
- `nv` = Neutral Variant
- `e` = Error

**Shades:** 0, 5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 70, 80, 90, 95, 98, 99, 100

### 3. Typography (`typography-`)

**Source:** `styles/type.ts`

| Category            | Prefix           | Scale/Values                              | Examples                                                                             |
| :------------------ | :--------------- | :---------------------------------------- | :----------------------------------------------------------------------------------- |
| **Font Family**     | `typography-f-`  | `sf` (Sans/Flex), `s` (Serif), `c` (Code) | `typography-f-sf` (System UI / Outfit)                                               |
| **Weight**          | `typography-w-`  | 100-900                                   | `typography-w-400` (Regular), `typography-w-500` (Medium), `typography-w-700` (Bold) |
| **Size (Body)**     | `typography-sz-` | `bs`, `bm`, `bl`                          | `typography-sz-bm` (Body Medium - 14px)                                              |
| **Size (Title)**    | `typography-sz-` | `ts`, `tm`, `tl`                          | `typography-sz-tl` (Title Large - 22px)                                              |
| **Size (Headline)** | `typography-sz-` | `hs`, `hm`, `hl`                          | `typography-sz-hl` (Headline Large - 32px)                                           |
| **Size (Display)**  | `typography-sz-` | `ds`, `dm`, `dl`                          | `typography-sz-dl` (Display Large - 57px)                                            |
| **Align**           | `typography-ta-` | `s` (Start), `c` (Center)                 | `typography-ta-c`                                                                    |

### 4. Borders (`border-`)

**Source:** `styles/border.ts`

| Category   | Prefix       | Scale/Values   | Examples                                              |
| :--------- | :----------- | :------------- | :---------------------------------------------------- |
| **Radius** | `border-br-` | 0-24 (1 = 4px) | `border-br-4` (16px), `border-br-50pc` (50% / Circle) |
| **Width**  | `border-bw-` | 0-24 (Pixels)  | `border-bw-1` (1px), `border-bw-2` (2px)              |
| **Style**  | `border-bs-` | `s` (Solid)    | `border-bs-s`                                         |

### 5. Behavior & Opacity

**Source:** `styles/behavior.ts`, `styles/opacity.ts`

| Category          | Prefix         | Scale/Values                           | Examples                                |
| :---------------- | :------------- | :------------------------------------- | :-------------------------------------- |
| **Hover Opacity** | `behavior-ho-` | 0-100 (Step 5)                         | `behavior-ho-80` (Opacity 0.8 on hover) |
| **Opacity**       | `opacity-el-`  | 0-100 (Step 5)                         | `opacity-el-50` (Opacity 0.5)           |
| **Overflow**      | `behavior-o-`  | `s` (Scroll), `a` (Auto), `h` (Hidden) | `behavior-o-h`                          |
| **Scrollbar**     | `behavior-sw-` | `n` (None)                             | `behavior-sw-n`                         |

### 6. Icons

**Source:** `styles/icons.ts`

- Class: `.g-icon`
- Variants: `.filled`, `.filled-heavy`
- Usage: `<span class="g-icon">icon_name</span>`
