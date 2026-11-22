// Offline Playwright double: every POST creates another draft, even on replay.
const fs = require('node:fs');
const path = require('node:path');
const { randomInt } = require('node:crypto');
const root = process.env.FAKE_ERP_ROOT;
const mode = process.env.FAKE_SAVE_MODE;
const pending = () => new Promise(() => {});

module.exports.chromium = { launch: async () => {
  let intercept;
  const page = {
    setDefaultTimeout() {},
    route: async (_pattern, handler) => { intercept = handler; },
    goto: async () => {},
    locator: () => ({
      first() { return this; },
      fill: async () => {},
      waitFor: pending,
      click: async () => intercept({
        request: () => ({
          url: () => 'https://erp.invalid/api/cards',
          method: () => 'POST',
          postDataJSON: () => ({ purpose: 'test' }),
        }),
        fetch: async () => {
          fs.appendFileSync(path.join(root, 'requests'), 'POST\n');
          const id = randomInt(1, 2 ** 32);
          fs.appendFileSync(path.join(root, 'drafts'), id + '\n');
          if (mode === 'lost') process.exit(81); // ERP committed, no response.
          return { json: async () => ({ code: 200, data: id }) };
        },
        fulfill: async () => {
          if (mode === 'crash_after_receipt') process.exit(82);
        },
      }),
    }),
  };
  return {
    newContext: async () => ({ newPage: async () => page, addInitScript: async () => {} }),
    close: async () => {},
  };
}};
