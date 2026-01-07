<div align="center">

<img src="wireit.svg" height="125" alt="wireit"/>

_Wireit upgrades your npm scripts to make them smarter and more efficient._

[![Published on npm](https://img.shields.io/npm/v/wireit.svg?logo=npm)](https://www.npmjs.com/package/wireit)
[![Build Status](https://github.com/google/wireit/actions/workflows/tests.yml/badge.svg)](https://github.com/google/wireit/actions/workflows/tests.yml)
[![Discord](https://discordapp.com/api/guilds/1036766782867914792/widget.png?style=shield)](https://discord.gg/chmdAwvq4e)

</div>

## Features

- 🙂 Use the `npm run` commands you already know
- ⛓️ Automatically run dependencies between npm scripts in parallel
- 👀 Watch any script and continuously re-run on changes
- 🥬 Skip scripts that are already fresh
- ♻️ Cache output locally and remotely on GitHub Actions for free
- 🛠️ Works with single packages, npm workspaces, and other monorepos
- ✏️ [VSCode plugin](https://marketplace.visualstudio.com/items?itemName=google.wireit) gives suggestions, documentation, and warnings as you develop

## Contents

- [Features](#features)
- [Install](#install)
- [Setup](#setup)
- [VSCode Extension](#vscode-extension)
- [Discord](#discord)
- [Dependencies](#dependencies)
  - [Vanilla scripts](#vanilla-scripts)
  - [Cross-package dependencies](#cross-package-dependencies)
- [Parallelism](#parallelism)
- [Extra arguments](#extra-arguments)
- [Input and output files](#input-and-output-files)
  - [Example configuration](#example-configuration)
  - [Default excluded paths](#default-excluded-paths)
- [Incremental build](#incremental-build)
- [Caching](#caching)
  - [Local caching](#local-caching)
  - [GitHub Actions caching](#github-actions-caching)
- [Cleaning output](#cleaning-output)
- [Watch mode](#watch-mode)
- [Services](#services)
- [Execution cascade](#execution-cascade)
- [Environment variables](#environment-variables)
- [Failures and errors](#failures-and-errors)
- [Package locks](#package-locks)
- [Recipes](#recipes)
  - [TypeScript](#typescript)
  - [ESLint](#eslint)
- [Reference](#reference)
  - [Configuration](#configuration)
  - [Dependency syntax](#dependency-syntax)
  - [Environment variable reference](#environment-variable-reference)
  - [Glob patterns](#glob-patterns)
  - [Fingerprint](#fingerprint)
- [Requirements](#requirements)
- [Related tools](#related-tools)
- [Contributing](#contributing)

## Install

```sh
npm i -D wireit
```

## Setup

Wireit works _with_ `npm run`, it doesn't replace it. To configure an NPM script
for Wireit, move the command into a new `wireit` section of your `package.json`,
and replace the original script with the `wireit` command.

<table>
<tr>
<th>Before</th>
<th>After</th>
</tr>
<tr>
<td>
<pre lang="json">
{
  "scripts": {
    "build": "tsc"
  }
}
</pre>
</td>
<td>
<pre lang="json">
{
  "scripts": {
    "build": "wireit"
  },
  "wireit": {
    "build": {
      "command": "tsc"
    }
  }
}
</pre>
</td>
</tr>
</table>

Now when you run `npm run build`, Wireit upgrades the script to be smarter and
more efficient. Wireit also works with [`node --run`](https://nodejs.org/en/blog/announcements/v22-release-announce#running-packagejson-scripts), [yarn](https://yarnpkg.com/), and [pnpm](https://pnpm.io/).

You should also add `.wireit` to your `.gitignore` file. Wireit uses the
`.wireit` directory to store caches and other data for your scripts.

```sh
echo .wireit >> .gitignore
```

## VSCode Extension

If you use VSCode, consider installing the `google.wireit` extension. It adds documentation on hover, autocomplete, can diagnose a number of common mistakes, and even suggest a refactoring to convert an npm script to use wireit.

Install it [from the marketplace](https://marketplace.visualstudio.com/items?itemName=google.wireit) or on the command line like:

```sh
code --install-extension google.wireit
```

## Discord

Join the [Wireit Discord](https://discord.gg/chmdAwvq4e) to chat with the Wireit community and get support for your project.

[![Discord](https://discordapp.com/api/guilds/1036766782867914792/widget.png?style=banner2)](https://discord.gg/chmdAwvq4e)

## Dependencies

To declare a dependency between two scripts, edit the
`wireit.<script>.dependencies` list:

```json
{
  "scripts": {
    "build": "wireit",
    "bundle": "wireit"
  },
  "wireit": {
    "build": {
      "command": "tsc"
    },
    "bundle": {
      "command": "rollup -c",
      "dependencies": ["build"]
    }
  }
}
```

Now when you run `npm run bundle`, the `build` script will automatically run
first.

### Vanilla scripts

The scripts you depend on don't need to be configured for Wireit, they can be
vanilla `npm` scripts. This lets you only use Wireit for some of your scripts,
or to upgrade incrementally. Scripts that haven't been configured for Wireit are
always safe to use as dependencies; they just won't be fully optimized.

### Wireit-only scripts

It is valid to define a script in the `wireit` section that is not in the
`scripts` section, but such scripts can only be used as `dependencies` from
other wireit scripts, and can never be run directly.

### Cross-package dependencies

Dependencies can refer to scripts in other npm packages by using a relative path
with the syntax `<relative-path>#<script-name>`. All cross-package dependencies
should start with a `"."`. Cross-package dependencies work well for npm
workspaces, as well as in other kinds of monorepos.

> [!NOTE]
> The `:` character can also be used as a delimiter instead of `#` (e.g.
> `"../other-package:build`), but `#` is preferred since February 2025 because
> it is less ambiguous given the prevalence of `:` in npm script names.

```json
{
  "scripts": {
    "build": "wireit"
  },
  "wireit": {
    "build": {
      "command": "tsc",
      "dependencies": ["../other-package#build"]
    }
  }
}
```

## Parallelism

Wireit will run scripts in parallel whenever it is safe to do so according to
the dependency graph.

For example, in this diagram, the `B` and `C` scripts will run in parallel,
while the `A` script won't start until both `B` and `C` finish.

```mermaid
graph TD
  A-->B;
  A-->C;
  subgraph parallel
    B;
    C;
  end
```

By default, Wireit will run up to 2 scripts in parallel for every logical CPU
core detected on your system. To change this default, set the `WIREIT_PARALLEL`
[environment variable](#environment-variable-reference) to a positive integer, or
`infinity` to run without a limit. You may want to lower this number if you
experience resource starvation in large builds. For example, to run only one
script at a time:

```sh
export WIREIT_PARALLEL=1
npm run build
```

If two or more separate `npm run` commands are run for the same Wireit script
simultaneously, then only one instance will be allowed to run at a time, while
the others wait their turn. This prevents coordination problems that can result
in incorrect output files being produced. If `output` is set to an empty array,
then this restriction is removed.

## Extra arguments

As with plain npm scripts, you can pass extra arguments to a Wireit script by
placing a `--` double-dash argument in front of them. Any arguments after a `--`
are sent to the underlying command, instead of being interpreted as arguments to
npm or Wireit:

```sh
npm run build -- --verbose
```

Or in general:

```sh
npm run {script} {npm args} {wireit args} -- {script args}
```

An additional `--` is required when using `node --run` in order to distinguish
between arguments intended for `node`, `wireit`, and the script itself:

```sh
node --run {script} {node args} -- {wireit args} -- {script args}
```

## Input and output files

The `files` and `output` properties of `wireit.<script>` tell Wireit what your
script's input and output files are, respectively. They should be arrays of
[glob patterns](#glob-patterns), where paths are interpreted relative to the
package directory. They can be set on some, all, or none of your scripts.

Setting these properties allow you to use more features of Wireit:

|                                             | Requires<br>`files` | Requires<br>`output` |
| ------------------------------------------: | :-----------------: | :------------------: |
|       [**Dependency graph**](#dependencies) |          -          |          -           |
|               [**Watch mode**](#watch-mode) |         ☑️          |          -           |
|         [**Clean build**](#cleaning-output) |          -          |          ☑️          |
| [**Incremental build**](#incremental-build) |         ☑️          |          ☑️          |
|                     [**Caching**](#caching) |         ☑️          |          ☑️          |

### Example configuration

```json
{
  "scripts": {
    "build": "wireit",
    "bundle": "wireit"
  },
  "wireit": {
    "build": {
      "command": "tsc",
      "files": ["src/**/*.ts", "tsconfig.json"],
      "output": ["lib/**"]
    },
    "bundle": {
      "command": "rollup -c",
      "dependencies": ["build"],
      "files": ["rollup.config.json"],
      "output": ["dist/bundle.js"]
    }
  }
}
```

### Default excluded paths

By default, the following folders are excluded from the `files` and `output`
arrays:

- `.git/`
- `.hg/`
- `.svn/`
- `.wireit/`
- `.yarn/`
- `CVS/`
- `node_modules/`

In the highly unusual case that you need to reference a file in one of those
folders, set `allowUsuallyExcludedPaths: true` to remove all default excludes.

## Incremental build

Wireit can automatically skip execution of a script if nothing has changed that
would cause it to produce different output since the last time it ran. This is
called _incremental build_.

To enable incremental build, configure the input and output files for each
script by specifying [glob patterns](#glob-patterns) in the
`wireit.<script>.files` and `wireit.<script>.output` arrays.

> ℹ️ If a script doesn't have a `files` or `output` list defined at all, then it
> will _always_ run, because Wireit doesn't know which files to check for
> changes. To tell Wireit it is safe to skip execution of a script that
> definitely has no input and/or files, set `files` and/or `output` to an empty
> array (`files: [], output: []`).

## Caching

If a script has previously succeeded with the same configuration and input
files, then Wireit can copy the output from a cache, instead of running the
command. This can significantly improve build and test time.

To enable caching for a script, ensure you have defined both the [`files` and
`output`](#input-and-output-files) arrays.

> ℹ️ If a script doesn't produce any output files, it can still be cached by
> setting `output` to an empty array (`"output": []`). Empty output is common for
> tests, and is useful because it allows you to skip running tests if they
> previously passed with the exact same inputs.

### Local caching

In _local_ mode, Wireit caches `output` files to the `.wireit` folder inside
each of your packages.

Local caching is enabled by default, unless the
[`CI=true`](https://docs.github.com/en/enterprise-cloud@latest/actions/learn-github-actions/environment-variables#default-environment-variables)
environment variable is detected. To force local caching, set
`WIREIT_CACHE=local`. To disable local caching, set `WIREIT_CACHE=none`.

> ⚠️ Wireit does not currently limit the size of local caches. To free up this
> space, use `rm -rf .wireit/*/cache`. Automatic cache size limits will be added
> in an upcoming release, tracked at
> [wireit#71](https://github.com/google/wireit/issues/71).

### GitHub Actions caching

In _[GitHub Actions](https://github.com/features/actions)_ mode, Wireit caches
`output` files to the [GitHub Actions
cache](https://docs.github.com/en/actions/using-workflows/caching-dependencies-to-speed-up-workflows)
service. This service is available whenever running in GitHub Actions, and is
free for all GitHub users.

> ℹ️ GitHub Actions cache entries are automatically deleted after 7 days, or if
> total usage exceeds 10 GB (the least recently used cache entry is deleted
> first). See the [GitHub Actions
> documentation](https://docs.github.com/en/actions/using-workflows/caching-dependencies-to-speed-up-workflows#usage-limits-and-eviction-policy)
> for more details.

To enable caching on GitHub Actions, add the following
[`uses`](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions#jobsjob_idstepsuses)
clause to your workflow. It can appear anywhere before the first `npm run` or
`npm test` command:

```yaml
- uses: google/wireit@setup-github-actions-caching/v2
```

#### Example workflow

```yaml
# File: .github/workflows/tests.yml

name: Tests
on: [push, pull_request]
jobs:
  tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
        with:
          node-version: 18
          cache: npm

      # Set up GitHub Actions caching for Wireit.
      - uses: google/wireit@setup-github-actions-caching/v2

      # Install npm dependencies.
      - run: npm ci

      # Run tests. Wireit will automatically use
      # the GitHub Actions cache whenever possible.
      - run: npm test
```

## Cleaning output

Wireit can automatically delete output files from previous runs before executing
a script. This is helpful for ensuring that every build is clean and free from
outdated files created in previous runs from source files that have since been
removed.

Cleaning is enabled by default as long as the
[`output`](#input-and-output-files) array is defined. To change this behavior,
set the `wireit.<script>.clean` property to one of these values:

| Setting             | Description                                                                                                                                                                                                                                                                                     |
| ------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `true`              | Clean before every run (the default).                                                                                                                                                                                                                                                           |
| `"if-file-deleted"` | Clean only if an input file has been deleted since the last run.<br><br>Use this option for tools that have incremental build support, but do not clean up outdated output when a source file has been deleted, such as `tsc --build` (see [TypeScript](#typescript) for more on this example.) |
| `false`             | Do not clean.<br><br>Only use this option if you are certain that the script command itself already takes care of removing outdated files from previous runs.                                                                                                                                   |

## Watch mode

In _watch_ mode, Wireit monitors all `files` of a script, and all `files` of its
transitive dependencies, and when there is a change, it re-runs only the
affected scripts. To enable watch mode, ensure that the
[`files`](#input-and-output-files) array is defined, and add the `--watch`
flag:

```sh
npm run <script> --watch
```

An additional `--` is required when using `node --run`, otherwise Node's
built-in [watch](https://nodejs.org/docs/v20.17.0/api/cli.html#--watch) feature
will be triggered instead of Wireit's:

```sh
node --run <script> -- --watch
```

The benefit of Wireit's watch mode over the built-in watch modes of Node and other programs are:

- Wireit watches the entire dependency graph, so a single watch command replaces
  many built-in ones.
- It prevents problems that can occur when running many separate watch commands
  simultaneously, such as build steps being triggered before all preceding steps
  have finished.

By default, watch mode uses whichever filesystem change API is available on your
OS. This behavior can be changed with the `WIREIT_WATCH_STRATEGY` and
`WIREIT_WATCH_POLL_MS` environment variables (see
[below](#environment-variable-reference)).

## Environment variables

Use the `env` setting to either directly set environment variables, or to
indicate that an externally-defined environment variable affects the behavior of
a script.

### Setting environment variables directly

If a property value in the `env` object is a string, then that environment
variable will be set to that value when the script's `command` runs,
overriding any value from the parent process.

Unlike built-in shell environment variable syntaxes, using `env` to set
environment variables works the same in macOS/Linux vs Windows, and in all
shells.

> **Note** Setting an environment variable with `env` does not apply
> transitively through dependencies. If you need the same environment variable
> to be set for multiple scripts, you must configure it for each of them.

```json
{
  "wireit": {
    "my-script": {
      "command": "my-command",
      "env": {
        "MY_VARIABLE": "my value"
      }
    }
  }
}
```

### Indicating external environment variables

If an environment variable affects the behavior of a script but is set
_externally_ (i.e. it is passed to the `wireit` parent process), set the `env`
property to `{"external": true}`. This tells Wireit that if the value of an
environment variable changes across executions of a script, then its output
should not be re-used. You may also set a `default` value for the variable
to use when none is provided externally.

```json
{
  "wireit": {
    "my-script": {
      "command": "my-command",
      "env": {
        "MY_VARIABLE": {
          "external": true
        },
        "MY_VARIABLE_2": {
          "external": true,
          "default": "foo"
        }
      }
    }
  }
}
```

## Services

By default, Wireit assumes that your scripts will eventually exit by themselves.
This is well suited for build and test scripts, but not for long-running
processes like servers. To tell Wireit that a process is long-running and not
expected to exit by itself, set `"service": true`.

```json
{
  "scripts": {
    "start": "wireit",
    "build:server": "wireit"
  },
  "wireit": {
    "start": {
      "command": "node my-server.js",
      "service": true,
      "files": ["my-server.js"],
      "dependencies": [
        "build:server",
        {
          "script": "../assets#build",
          "cascade": false
        }
      ]
    },
    "build:server": {
      ...
    }
  }
}
```

### Service lifetime

If a service is run _directly_ (e.g. `npm run serve`), then it will stay running
until the user kills Wireit (e.g. `Ctrl-C`).

If a service is a _dependency_ of one or more other scripts, then it will start
up before any depending script runs, and will shut down after all depending
scripts finish.

### Service readiness

By default, a service is considered _ready_ as soon as its process spawns,
allowing any scripts that depend on that service to start.

However, often times a service needs to perform certain actions before it is
safe for dependents to interact with it, such as starting a server and listening
on a network interface.

Use `service.readyWhen.lineMatches` to tell Wireit to monitor the `stdout` and
`stderr` of the service and defer readiness until a line is printed that matches
the given regular expression.

```json
{
  "command": "node my-server.js",
  "service": {
    "readyWhen": {
      "lineMatches": "Server listening on port \\d+"
    }
  }
}
```

### Service restarts

In watch mode, a service will be restarted whenever one of its input files or
dependencies change, except for dependencies with
[`cascade`](#execution-cascade) set to `false`.

### Service output

Services cannot have `output` files, because there is no way for Wireit to know
when a service has finished writing its output.

If you have a service that produces output, you should define a _non-service_
script that depends on it, and which exits when the service's output is
complete.

## Execution cascade

By default, a script always needs to run (or restart in the case of
[`services`](#services)) if any of its dependencies needed to run, _regardless
of whether the dependency produced new or relevant output_.

This automatic _cascade_ of script execution is the default behavior because it
ensures that any _possible_ output produced by a dependent script propagates to all other
scripts that might depend on it. In other words, Wireit does not assume that the
`files` array completely describes the inputs to a script with dependencies.

### Disabling cascade

This execution cascade behavior can be disabled by expanding a dependency into
an object, and setting the `cascade` property to `false`:

> **Note**
> What really happens under the hood is that the `cascade` property simply controls
> whether the [fingerprint](#fingerprint) of a script _includes the fingerprints
> of its dependencies_, which in turn determines whether a script needs to run or restart.

```json
{
  "dependencies": [
    {
      "script": "foo",
      "cascade": false
    }
  ]
}
```

### Reasons to disable cascade

There are two main reasons you might want to set `cascade` to `false`:

1. **Your script only consumes a subset of a dependency's output.**

   For example, `tsc` produces both `.js` files and `.d.ts` files, but only the
   `.js` files might be consumed by `rollup`. There is no need to re-bundle
   when a typings-only changed occurred.

   > **Note**
   > In addition to setting `cascade` to `false`, the subset of output that
   > _does_ matter (`lib/**/*.js`) has been added to the `files` array.

   ```json
   {
     "scripts": {
       "build": "wireit",
       "bundle": "wireit"
     },
     "wireit": {
       "build": {
         "command": "tsc",
         "files": ["src/**/*.ts", "tsconfig.json"],
         "output": ["lib/**"]
       },
       "bundle": {
         "command": "rollup -c",
         "dependencies": [
           {
             "script": "build",
             "cascade": false
           }
         ],
         "files": ["rollup.config.json", "lib/**/*.js"],
         "output": ["dist/bundle.js"]
       }
     }
   }
   ```

2. **Your server doesn't need to restart for certain changes.**

   For example, a web server depends on some static assets, but the server
   reads those assets from disk dynamically on each request. In [`watch`](#watch-mode) mode,
   there is no need to restart the server when the assets change.

   > **Note**
   > The `build:server` dependency uses the default `cascade` behavior
   > (`true`), because changing the implementation of the server itself _does_
   > require the server to be restarted.

   ```json
   {
     "scripts": {
       "start": "wireit",
       "build:server": "wireit"
     },
     "wireit": {
       "start": {
         "command": "node lib/server.js",
         "service": true,
         "dependencies": [
           "build:server",
           {
             "script": "../assets#build",
             "cascade": false
           }
         ],
         "files": ["lib/**/*.js"]
       },
       "build:server": {
         "command": "tsc",
         "files": ["src/**/*.ts", "tsconfig.json"],
         "output": ["lib/**"]
       }
     }
   }
   ```

## Failures and errors

By default, when a script fails (meaning it returned with a non-zero exit code),
all scripts that are already running are allowed to finish, but new scripts are
not started.

In some situations a different behavior may be better suited. There are 2
additional modes, which you can set with the `WIREIT_FAILURES` environment
variable. Note that Wireit always ultimately exits with a non-zero exit code if
there was a failure, regardless of the mode.

### Continue

When a failure occurs in `continue` mode, running scripts continue, and new
scripts are started as long as the failure did not affect their dependencies.
This mode is useful if you want a complete picture of which scripts are
succeeding and which are failing.

```bash
WIREIT_FAILURES=continue
```

### Kill

When a failure occurs in `kill` mode, running scripts are immediately killed,
and new scripts are not started. This mode is useful if you want to be notified
as soon as possible about any failures.

```bash
WIREIT_FAILURES=kill
```

## Package locks

By default, Wireit automatically treats package manager lock files as input
files
([`package-lock.json`](https://docs.npmjs.com/cli/v8/configuring-npm/package-lock-json)
for npm and `node --run`,
[`yarn.lock`](https://yarnpkg.com/configuration/yarnrc#lockfileFilename) for
yarn, and [`pnpm-lock.yaml`](https://pnpm.io/git#lockfiles) for pnpm). Wireit
will look for these lock files in the script's package, and all parent
directories.

This is useful because installing or upgrading your dependencies can affect the
behavior of your scripts, so it's important to re-run them whenever your
dependencies change.

To change the name of the package lock file Wireit should look for, specify it
in the `wireit.<script>.packageLocks` array. You can specify multiple filenames
here, if needed.

```json
{
  "scripts": {
    "build": "wireit"
  },
  "wireit": {
    "build": {
      "command": "tsc",
      "files": ["src/**/*.ts", "tsconfig.json"],
      "output": ["lib/**"],
      "packageLocks": ["another-package-manager.lock"]
    }
  }
}
```

If you're sure that a script isn't affected by dependencies at all, you can turn
off this behavior entirely to improve your cache hit rate by setting
`wireit.<script>.packageLocks` to `[]`.

## Recipes

This section contains advice about integrating specific build tools with Wireit.

### TypeScript

```json
{
  "scripts": {
    "ts": "wireit"
  },
  "wireit": {
    "ts": {
      "command": "tsc --build --pretty",
      "clean": "if-file-deleted",
      "files": ["src/**/*.ts", "tsconfig.json"],
      "output": ["lib/**", ".tsbuildinfo"]
    }
  }
}
```

- Set [`"incremental": true`](https://www.typescriptlang.org/tsconfig#incremental) and use
  [`--build`](https://www.typescriptlang.org/docs/handbook/project-references.html#build-mode-for-typescript)
  to enable incremental compilation, which significantly improves performance.
- Include
  [`.tsbuildinfo`](https://www.typescriptlang.org/tsconfig#tsBuildInfoFile) in
  `output` so that it is reset on clean builds. Otherwise `tsc` will get out of
  sync and produce incorrect output.
- Set [`"clean": "if-file-deleted"`](#cleaning-output) so that you get fast
  incremental compilation when sources are changed/added, but also stale outputs
  are cleaned up when a source is deleted (`tsc` does not clean up stale outputs
  by itself).
- Include `tsconfig.json` in `files` so that changing your configuration re-runs
  `tsc`.
- Use [`--pretty`](https://www.typescriptlang.org/tsconfig#pretty) to get
  colorful output despite not being attached to a TTY.

### ESLint

```json
{
  "scripts": {
    "lint": "wireit"
  },
  "wireit": {
    "lint": {
      "command": "eslint --color --cache --cache-location .eslintcache .",
      "files": ["src/**/*.ts", ".eslintignore", ".eslintrc.cjs"],
      "output": []
    }
  }
}
```

- Use
  [`--cache`](https://eslint.org/docs/user-guide/command-line-interface#caching)
  so that `eslint` only lints the files that were added or changed since the
  last run, which significantly improves performance.
- Use
  [`--color`](https://eslint.org/docs/user-guide/command-line-interface#--color---no-color)
  to get colorful output despite not being attached to a TTY.
- Include config and ignore files in `files` so that changing your configuration
  re-runs `eslint`.

## Reference

### Configuration

The following properties can be set inside `wireit.<script>` objects in
`package.json` files:

| Property                  | Type                               | Default                 | Description                                                                                                                     |
| ------------------------- | ---------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `command`                 | `string`                           | `undefined`             | The shell command to run.                                                                                                       |
| `dependencies`            | `string[] \| object[]`             | `[]`                    | [Scripts that must run before this one](#dependencies).                                                                         |
| `dependencies[i].script`  | `string`                           | `undefined`             | [The name of the script, when the dependency is an object](#dependencies).                                                      |
| `dependencies[i].cascade` | `boolean`                          | `true`                  | [Whether this dependency always causes this script to re-execute](#execution-cascade).                                          |
| `files`                   | `string[]`                         | `undefined`             | Input file [glob patterns](#glob-patterns), used to determine the [fingerprint](#fingerprint).                                  |
| `output`                  | `string[]`                         | `undefined`             | Output file [glob patterns](#glob-patterns), used for [caching](#caching) and [cleaning](#cleaning-output).                     |
| `clean`                   | `boolean \| "if-file-deleted"`     | `true`                  | [Delete output files before running](#cleaning-output).                                                                         |
| `env`                     | `Record<string, string \| object>` | `false`                 | [Environment variables](#environment-variables) to set when running this command, or that are external and affect the behavior. |
| `env[i].external`         | `true \| undefined`                | `undefined`             | `true` if an [environment variable](#environment-variables) is set externally and affects the script's behavior.                |
| `env[i].default`          | `string \| undefined`              | `undefined`             | Default value to use when an external [environment variable](#environment-variables) is not provided.                           |
| `service`                 | `boolean`                          | `false`                 | [Whether this script is long-running, e.g. a server](#cleaning-output).                                                         |
| `packageLocks`            | `string[]`                         | `['package-lock.json']` | [Names of package lock files](#package-locks).                                                                                  |

### Dependency syntax

The following syntaxes can be used in the `wireit.<script>.dependencies` array:

| Example      | Description                                                                                     |
| ------------ | ----------------------------------------------------------------------------------------------- |
| `foo`        | Script named `"foo"` in the same package.                                                       |
| `../foo:bar` | Script named `"bar"` in the package found at `../foo` ([details](#cross-package-dependencies)). |

### Environment variable reference

The following environment variables affect the behavior of Wireit:

| Variable                | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `WIREIT_CACHE`          | [Caching mode](#caching).<br><br>Defaults to `local` unless `CI` is `true`, in which case defaults to `none`.<br><br>Automatically set to `github` by the [`google/wireit@setup-github-actions-caching/v2`](#github-actions-caching) action.<br><br>Options:<ul><li>[`local`](#local-caching): Cache to local disk.</li><li>[`github`](#github-actions-caching): Cache to GitHub Actions.</li><li>`none`: Disable caching.</li></ul>                                                                                                                                                                                                                                                      |
| `WIREIT_FAILURES`       | [How to handle script failures](#failures-and-errors).<br><br>Options:<br><ul><li>[`no-new`](#failures-and-errors) (default): Allow running scripts to finish, but don't start new ones.</li><li>[`continue`](#continue): Allow running scripts to continue, and start new ones unless any of their dependencies failed.</li><li>[`kill`](#kill): Immediately kill running scripts, and don't start new ones.</li></ul>                                                                                                                                                                                                                                                                   |
| `WIREIT_LOGGER`         | How to present progress and results on the command line.<br><br>Options:<br><ul><li>`quiet` (default for normal execution): Writes a single dynamically updating line summarizing progress. Only passes along stdout and stderr from commands if there's a failure, or if the command is a service.</li><li>`quiet-ci` (default when `env.CI` or `!stdout.isTTY`): like `quiet` but optimized for non-interactive environments, like GitHub Actions runners.</li><li>`simple`: A verbose logger that presents clear information about the work that Wireit is doing.</li><li>`metrics`: Like `simple`, but also presents a summary table of results once a command is finished.</li></ul> |
| `WIREIT_DEBUG_LOG_FILE` | Path to a file which will receive detailed event logging.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `WIREIT_MAX_OPEN_FILES` | Limits the number of file descriptors Wireit will have open concurrently. Prevents resource exhaustion when checking large numbers of cached files. Set to a lower number if you hit file descriptor limits.                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `WIREIT_PARALLEL`       | [Maximum number of scripts to run at one time](#parallelism).<br><br>Defaults to 2×logical CPU cores.<br><br>Must be a positive integer or `infinity`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `WIREIT_WATCH_STRATEGY` | How Wireit determines when a file has changed which warrants a new watch iteration.<br><br>Options:<br><ul><li>`event` (default): Register OS file system watcher callbacks (using [chokidar](https://github.com/paulmillr/chokidar)).</li><li>`poll`: Poll the filesystem every `WIREIT_WATCH_POLL_MS` milliseconds. Less responsive and worse performance than `event`, but a good fallback for when `event` does not work well or at all (e.g. filesystems that don't support filesystem events, or performance and memory problems with large file trees).</li></ul>                                                                                                                  |
| `WIREIT_WATCH_POLL_MS`  | When `WIREIT_WATCH_STRATEGY` is `poll`, how many milliseconds to wait between each filesystem poll. Defaults to `500`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `CI`                    | Affects the default value of `WIREIT_CACHE`.<br><br>Automatically set to `true` by [GitHub Actions](https://docs.github.com/en/actions/learn-github-actions/environment-variables#default-environment-variables) and most other CI (continuous integration) services.<br><br>Must be exactly `true`. If unset or any other value, interpreted as `false`.                                                                                                                                                                                                                                                                                                                                 |

### Glob patterns

The following glob syntaxes are supported in the `files` and `output` arrays:

| Example         | Description                                                                              |
| --------------- | ---------------------------------------------------------------------------------------- |
| `foo`           | The file named `foo`, or if `foo` is a directory, all recursive children of `foo`.       |
| `foo/*.js`      | All files directly in the `foo/` directory which end in `.js`.                           |
| `foo/**/*.js`   | All files in the `foo/` directory, and all recursive subdirectories, which end in `.js`. |
| `foo.{html,js}` | Files named `foo.html` or `foo.js`.                                                      |
| `!foo`          | Exclude the file or directory `foo` from previous matches.                               |

Also note these details:

- Paths should always use `/` (forward-slash) delimiters, even on Windows.
- Paths are interpreted relative to the current package even if there is a
  leading `/` (e.g. `/foo` is the same as `foo`).
- Whenever a directory is matched, all recursive children of that directory are
  included.
- `files` are allowed to reach outside of the current package using e.g.
  `../foo`. `output` files cannot reference files outside of the current
  package.
- Symlinks in input `files` are followed, so that they are identified by their content.
- Symlinks in `output` files are cached as symlinks, so that restoring from
  cache doesn't create unnecessary copies.
- The order of `!exclude` patterns is significant.
- Hidden/dot files are matched by `*` and `**`.
- Patterns are case-sensitive (if supported by the filesystem).

### Fingerprint

The following inputs determine the _fingerprint_ for a script. This value is
used to determine whether a script can be skipped for [incremental
build](#incremental-build), and whether its output can be [restored from
cache](#caching).

- The `command` setting.
- The [extra arguments](#extra-arguments) set on the command-line.
- The `clean` setting.
- The `output` glob patterns.
- The SHA256 content hashes of all files matching `files`.
- The SHA256 content hashes of all files matching `packageLocks` in the current
  package and all parent directories.
- The environment variable values configured in `env`.
- The system platform (e.g. `linux`, `win32`).
- The system CPU architecture (e.g. `x64`).
- The system Node version (e.g. `20.11.1`).
- The fingerprint of all transitive dependencies, unless `cascade` is set to
  `false`.

When using [GitHub Actions caching](#github-actions-caching), the following
input also affects the fingerprint:

- The `ImageOS` environment variable (e.g. `ubuntu20`, `macos11`).

## Requirements

Wireit is supported on Linux, macOS, and Windows.

Wireit is supported on Node Current (22), Active LTS (20), and Maintenance LTS
(18). See [Node releases](https://nodejs.org/en/about/releases/) for the
schedule.

Wireit scripts can be launched via `npm`, `node --run`, `pnpm`, and `yarn`.

## Related tools

Wireit shares a number of features with these other great tools, and we highly
recommend you check them out too:

- [Nx](https://nx.dev/)
- [Turborepo](https://turborepo.org/)
- [Chomp](https://chompbuild.com/)
- [Bazel](https://bazel.build/)

Here are some things you might especially like about Wireit:

- **Feels like npm**. When you use Wireit, you'll continue typing the same npm
  commands you already use, like `npm run build` and `npm test`. There are no
  new command-line tools to learn, and there's only one way to run each script.
  Your script config stays in your `package.json`, too. Wireit is designed to be
  the minimal addition to npm needed to get script dependencies and incremental
  build.

- **Caching with GitHub Actions**. Wireit supports caching build artifacts and
  test results directly through GitHub Actions, without any extra third-party
  services. Just add a single `uses:` line to your workflows.

- **Watch any script**. Want to automatically re-run your build and tests
  whenever you make a change? Type `npm test --watch`. Any script you've
  configured using Wireit can be watched by typing `--watch` after it.

- **Great for single packages and monorepos**. Wireit has no opinion about how
  your packages are arranged. It works great with single packages, because you
  can link together scripts within the same package. It also works great with
  any kind of monorepo, because you can link together scripts across different
  packages using relative paths.

- **Complements npm workspaces**. We think Wireit could be the missing tool that
  unlocks the potential for [npm
  workspaces](https://docs.npmjs.com/cli/v8/using-npm/workspaces) to become the
  best way to set up monorepos. To use Wireit with npm workspaces, you'll just
  use standard npm workspace commands like `npm run build -ws`.

- **Adopt incrementally**. Wireit scripts can depend on plain npm scripts, so
  they can be freely mixed. This means you can use Wireit only for the parts of
  your build that need it most, or you can try it out on a script-by-script
  basis without changing too much at the same time.

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md)
