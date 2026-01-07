# A2A JavaScript SDK

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

<!-- markdownlint-disable no-inline-html -->

<html>
   <h2 align="center">
   <img src="https://raw.githubusercontent.com/google-a2a/A2A/refs/heads/main/docs/assets/a2a-logo-black.svg" width="256" alt="A2A Logo"/>
   </h2>
   <h3 align="center">A JavaScript library that helps run agentic applications as A2AServers following the <a href="https://google-a2a.github.io/A2A">Agent2Agent (A2A) Protocol</a>.</h3>
</html>

<!-- markdownlint-enable no-inline-html -->

## Installation

You can install the A2A SDK using `npm`.

```bash
npm install @a2a-js/sdk
```

### For Server Usage

If you plan to use the A2A server functionality (`A2AExpressApp`), you'll also need to install Express as it's a peer dependency:

```bash
npm install express
```

You can also find JavaScript samples [here](https://github.com/google-a2a/a2a-samples/tree/main/samples/js).

---

## Quickstart

This example shows how to create a simple "Hello World" agent server and a client to interact with it.

### Server: Hello World Agent

The core of an A2A server is the `AgentExecutor`, which contains your agent's logic.

```typescript
// server.ts
import express from 'express';
import { v4 as uuidv4 } from 'uuid';
import { AgentCard, Message, AGENT_CARD_PATH } from '@a2a-js/sdk';
import {
  AgentExecutor,
  RequestContext,
  ExecutionEventBus,
  DefaultRequestHandler,
  InMemoryTaskStore,
} from '@a2a-js/sdk/server';
import { agentCardHandler, jsonRpcHandler, restHandler, UserBuilder } from '@a2a-js/sdk/server/express';

// 1. Define your agent's identity card.
const helloAgentCard: AgentCard = {
  name: 'Hello Agent',
  description: 'A simple agent that says hello.',
  protocolVersion: '0.3.0',
  version: '0.1.0',
  url: 'http://localhost:4000/a2a/jsonrpc', // The public URL of your agent server
  skills: [{ id: 'chat', name: 'Chat', description: 'Say hello', tags: ['chat'] }],
  capabilities: {
    pushNotifications: false,
  },
  defaultInputModes: ['text'],
  defaultOutputModes: ['text'],
  additionalInterfaces: [
    { url: 'http://localhost:4000/a2a/jsonrpc', transport: 'JSONRPC' }, // Default JSON-RPC transport
    { url: 'http://localhost:4000/a2a/rest', transport: 'HTTP+JSON' }, // HTTP+JSON/REST transport
  ],
};

// 2. Implement the agent's logic.
class HelloExecutor implements AgentExecutor {
  async execute(requestContext: RequestContext, eventBus: ExecutionEventBus): Promise<void> {
    // Create a direct message response.
    const responseMessage: Message = {
      kind: 'message',
      messageId: uuidv4(),
      role: 'agent',
      parts: [{ kind: 'text', text: 'Hello, world!' }],
      // Associate the response with the incoming request's context.
      contextId: requestContext.contextId,
    };

    // Publish the message and signal that the interaction is finished.
    eventBus.publish(responseMessage);
    eventBus.finished();
  }

  // cancelTask is not needed for this simple, non-stateful agent.
  cancelTask = async (): Promise<void> => {};
}

// 3. Set up and run the server.
const agentExecutor = new HelloExecutor();
const requestHandler = new DefaultRequestHandler(
  helloAgentCard,
  new InMemoryTaskStore(),
  agentExecutor
);

const app = express();

app.use(`/${AGENT_CARD_PATH}`, agentCardHandler({ agentCardProvider: requestHandler }));
app.use('/a2a/jsonrpc', jsonRpcHandler({ requestHandler, userBuilder: UserBuilder.noAuthentication }));
app.use('/a2a/rest', restHandler({ requestHandler, userBuilder: UserBuilder.noAuthentication }));

app.listen(4000, () => {
  console.log(`🚀 Server started on http://localhost:4000`);
});
```

### Client: Sending a Message

The [`ClientFactory`](src/client/factory.ts) makes it easy to communicate with any A2A-compliant agent.

```typescript
// client.ts
import { ClientFactory, SendMessageSuccessResponse } from '@a2a-js/sdk/client';
import { Message, MessageSendParams } from '@a2a-js/sdk';
import { v4 as uuidv4 } from 'uuid';

async function run() {
  const factory = new ClientFactory();

  // createFromUrl accepts baseUrl and optional path,
  // (the default path is /.well-known/agent-card.json)
  const client = await factory.createFromUrl('http://localhost:4000');

  const sendParams: MessageSendParams = {
    message: {
      messageId: uuidv4(),
      role: 'user',
      parts: [{ kind: 'text', text: 'Hi there!' }],
      kind: 'message',
    },
  };

  try {
    const response = await client.sendMessage(sendParams);
    const result = response as Message;
    console.log('Agent response:', result.parts[0].text); // "Hello, world!"
  } catch(e) {
    console.error('Error:', e);
  }
}

await run();
```

---

## A2A `Task` Support

For operations that are stateful or long-running, agents create a `Task`. A task has a state (e.g., `working`, `completed`) and can produce `Artifacts` (e.g., files, data).

### Server: Creating a Task

This agent creates a task, attaches a file artifact to it, and marks it as complete.

```typescript
// server.ts
import { Task, TaskArtifactUpdateEvent, TaskStatusUpdateEvent } from '@a2a-js/sdk';
// ... other imports from the quickstart server ...

class TaskExecutor implements AgentExecutor {
  async execute(requestContext: RequestContext, eventBus: ExecutionEventBus): Promise<void> {
    const { taskId, contextId, userMessage, task } = requestContext;

    // 1. Create and publish the initial task object if it doesn't exist.
    if (!task) {
      const initialTask: Task = {
        kind: 'task',
        id: taskId,
        contextId: contextId,
        status: {
          state: 'submitted',
          timestamp: new Date().toISOString(),
        },
        history: [userMessage],
      };
      eventBus.publish(initialTask);
    }

    // 2. Create and publish an artifact.
    const artifactUpdate: TaskArtifactUpdateEvent = {
      kind: 'artifact-update',
      taskId: taskId,
      contextId: contextId,
      artifact: {
        artifactId: 'report-1',
        name: 'analysis_report.txt',
        parts: [{ kind: 'text', text: `This is the analysis for task ${taskId}.` }],
      },
    };
    eventBus.publish(artifactUpdate);

    // 3. Publish the final status and mark the event as 'final'.
    const finalUpdate: TaskStatusUpdateEvent = {
      kind: 'status-update',
      taskId: taskId,
      contextId: contextId,
      status: { state: 'completed', timestamp: new Date().toISOString() },
      final: true,
    };
    eventBus.publish(finalUpdate);
    eventBus.finished();
  }

  cancelTask = async (): Promise<void> => {};
}
```

### Client: Receiving a Task

The client sends a message and receives a `Task` object as the result.

```typescript
// client.ts
import { ClientFactory, SendMessageSuccessResponse } from '@a2a-js/sdk/client';
import { Message, MessageSendParams, Task } from '@a2a-js/sdk';
// ... other imports ...

const factory = new ClientFactory();

// createFromUrl accepts baseUrl and optional path,
// (the default path is /.well-known/agent-card.json)
const client = await factory.createFromUrl('http://localhost:4000');

try {
  const result = await client.sendMessage({
    message: {
      messageId: uuidv4(),
      role: 'user',
      parts: [{ kind: 'text', text: 'Do something.' }],
      kind: 'message',
    },
  });

  // Check if the agent's response is a Task or a direct Message.
  if (result.kind === 'task') {
    const task = result as Task;
    console.log(`Task [${task.id}] completed with status: ${task.status.state}`);

    if (task.artifacts && task.artifacts.length > 0) {
      console.log(`Artifact found: ${task.artifacts[0].name}`);
      console.log(`Content: ${task.artifacts[0].parts[0].text}`);
    }
  } else {
    const message = result as Message;
    console.log('Received direct message:', message.parts[0].text);
  }
} catch (e) {
  console.error('Error:', e);
}
```

---

## Client Customization

Client can be customized via [`CallInterceptor`'s](src/client/interceptors.ts) which is a recommended way as it's transport-agnostic.

Common use cases include:

- **Request Interception**: Log outgoing requests or collect metrics.
- **Header Injection**: Add custom headers for authentication, tracing, or routing.
- **A2A Extensions**: Modifying payloads to include protocol extension data.

### Example: Injecting a Custom Header

This example defines a `CallInterceptor` to update `serviceParameters` which are passed as HTTP headers.

```typescript
import { v4 as uuidv4 } from 'uuid';
import { AfterArgs, BeforeArgs, CallInterceptor, ClientFactory, ClientFactoryOptions } from '@a2a-js/sdk/client';

// 1. Define an interceptor
class RequestIdInterceptor implements CallInterceptor {
  before(args: BeforeArgs): Promise<void> {
    args.options = {
      ...args.options,
      serviceParameters: {
        ...args.options.serviceParameters,
        ['X-Request-ID']: uuidv4(),
      },
    };
    return Promise.resolve();
  }

  after(): Promise<void> {
    return Promise.resolve();
  }
}

// 2. Register the interceptor in the client factory
const factory = new ClientFactory(ClientFactoryOptions.createFrom(ClientFactoryOptions.default, {
  clientConfig: {
    interceptors: [new RequestIdInterceptor()]
  }
}))
const client = await factory.createFromAgentCardUrl('http://localhost:4000');

// Now, all requests made by clients created by this factory will include the X-Request-ID header.
await client.sendMessage({
  message: {
    messageId: uuidv4(),
    role: 'user',
    parts: [{ kind: 'text', text: 'A message requiring custom headers.' }],
    kind: 'message',
  },
});
```

### Example: Specifying a Timeout

Each client method can be configured with an optional `signal` field.

```typescript
import { ClientFactory } from '@a2a-js/sdk/client';

const factory = new ClientFactory();

// createFromUrl accepts baseUrl and optional path,
// (the default path is /.well-known/agent-card.json)
const client = await factory.createFromUrl('http://localhost:4000');

await client.sendMessage(
  {
    message: {
      messageId: uuidv4(),
      role: 'user',
      parts: [{ kind: 'text', text: 'A long-running message.' }],
      kind: 'message',
    },
  },
  {
    signal: AbortSignal.timeout(5000), // 5 seconds timeout
  }
);
```

### Customizing Transports: Using the Provided `AuthenticationHandler`

For advanced authentication scenarios, the SDK includes a higher-order function `createAuthenticatingFetchWithRetry` and an `AuthenticationHandler` interface. This utility automatically adds authorization headers and can retry requests that fail with authentication errors (e.g., 401 Unauthorized).

Here's how to use it to manage a Bearer token:

```typescript
import {
  ClientFactory,
  ClientFactoryOptions
  JsonRpcTransportFactory,
  AuthenticationHandler,
  createAuthenticatingFetchWithRetry,
} from '@a2a-js/sdk/client';

// A simple token provider that simulates fetching a new token.
const tokenProvider = {
  token: 'initial-stale-token',
  getNewToken: async () => {
    console.log('Refreshing auth token...');
    tokenProvider.token = `new-token-${Date.now()}`;
    return tokenProvider.token;
  },
};

// 1. Implement the AuthenticationHandler interface.
const handler: AuthenticationHandler = {
  // headers() is called on every request to get the current auth headers.
  headers: async () => ({
    Authorization: `Bearer ${tokenProvider.token}`,
  }),

  // shouldRetryWithHeaders() is called after a request fails.
  // It decides if a retry is needed and provides new headers.
  shouldRetryWithHeaders: async (req: RequestInit, res: Response) => {
    if (res.status === 401) {
      // Unauthorized
      const newToken = await tokenProvider.getNewToken();
      // Return new headers to trigger a single retry.
      return { Authorization: `Bearer ${newToken}` };
    }

    // Return undefined to not retry for other errors.
    return undefined;
  },
};

// 2. Create the authenticated fetch function.
const authFetch = createAuthenticatingFetchWithRetry(fetch, handler);

// 3. Inject new fetch implementation into a client factory.
const factory = new ClientFactory(ClientFactoryOptions.createFrom(ClientFactoryOptions.default, {
  transports: [
    new JsonRpcTransportFactory({ fetchImpl: authFetch })
  ]
}))

// 4. Clients created from the factory are going to have custom fetch attached.
const client = await factory.createFromUrl('http://localhost:4000');
```

---

## Streaming

For real-time updates, A2A supports streaming responses over Server-Sent Events (SSE).

### Server: Streaming Task Updates

The agent publishes events as it works on the task. The client receives these events in real-time.

```typescript
// server.ts
// ... imports ...

class StreamingExecutor implements AgentExecutor {
  async execute(requestContext: RequestContext, eventBus: ExecutionEventBus): Promise<void> {
    const { taskId, contextId, userMessage, task } = requestContext;

    // 1. Create and publish the initial task object if it doesn't exist.
    if (!task) {
      const initialTask: Task = {
        kind: 'task',
        id: taskId,
        contextId: contextId,
        status: {
          state: 'submitted',
          timestamp: new Date().toISOString(),
        },
        history: [userMessage],
      };
      eventBus.publish(initialTask);
    }

    // 2. Publish 'working' state.
    eventBus.publish({
      kind: 'status-update',
      taskId,
      contextId,
      status: { state: 'working', timestamp: new Date().toISOString() },
      final: false,
    });

    // 3. Simulate work and publish an artifact.
    await new Promise((resolve) => setTimeout(resolve, 1000));
    eventBus.publish({
      kind: 'artifact-update',
      taskId,
      contextId,
      artifact: { artifactId: 'result.txt', parts: [{ kind: 'text', text: 'First result.' }] },
    });

    // 4. Publish final 'completed' state.
    eventBus.publish({
      kind: 'status-update',
      taskId,
      contextId,
      status: { state: 'completed', timestamp: new Date().toISOString() },
      final: true,
    });
    eventBus.finished();
  }
  cancelTask = async (): Promise<void> => {};
}
```

### Client: Consuming a Stream

The `sendMessageStream` method returns an `AsyncGenerator` that yields events as they arrive from the server.

```typescript
// client.ts
import { ClientFactory } from '@a2a-js/sdk/client';
import { MessageSendParams } from '@a2a-js/sdk';
import { v4 as uuidv4 } from 'uuid';
// ... other imports ...

const factory = new ClientFactory();

// createFromUrl accepts baseUrl and optional path,
// (the default path is /.well-known/agent-card.json)
const client = await factory.createFromUrl('http://localhost:4000');

async function streamTask() {
  const streamParams: MessageSendParams = {
    message: {
      messageId: uuidv4(),
      role: 'user',
      parts: [{ kind: 'text', text: 'Stream me some updates!' }],
      kind: 'message',
    },
  };

  try {
    const stream = client.sendMessageStream(streamParams);

    for await (const event of stream) {
      if (event.kind === 'task') {
        console.log(`[${event.id}] Task created. Status: ${event.status.state}`);
      } else if (event.kind === 'status-update') {
        console.log(`[${event.taskId}] Status Updated: ${event.status.state}`);
      } else if (event.kind === 'artifact-update') {
        console.log(`[${event.taskId}] Artifact Received: ${event.artifact.artifactId}`);
      }
    }
    console.log('--- Stream finished ---');
  } catch (error) {
    console.error('Error during streaming:', error);
  }
}

await streamTask();
```

## Handling Task Cancellation

To support user-initiated cancellations, you must implement the `cancelTask` method in your **`AgentExecutor`**. The executor is responsible for gracefully stopping the ongoing work and publishing a final `canceled` status event.

A straightforward way to manage this is by maintaining an in-memory set of canceled task IDs. The `execute` method can then periodically check this set to see if it should terminate its process.

### Server: Implementing a Cancellable Executor

This example demonstrates an agent that simulates a multi-step process. In each step of its work, it checks if a cancellation has been requested. If so, it stops the work and updates the task's state accordingly.

```typescript
// server.ts
import {
  AgentExecutor,
  RequestContext,
  ExecutionEventBus,
  TaskStatusUpdateEvent,
} from '@a2a-js/sdk/server';
// ... other imports ...

class CancellableExecutor implements AgentExecutor {
  // Use a Set to track the IDs of tasks that have been requested to be canceled.
  private cancelledTasks = new Set<string>();

  /**
   * When a cancellation is requested, add the taskId to our tracking set.
   * The `execute` loop will handle the rest.
   */
  public async cancelTask(taskId: string, eventBus: ExecutionEventBus): Promise<void> {
    console.log(`[Executor] Received cancellation request for task: ${taskId}`);
    this.cancelledTasks.add(taskId);
  }

  public async execute(requestContext: RequestContext, eventBus: ExecutionEventBus): Promise<void> {
    const { taskId, contextId } = requestContext;

    // Start the task
    eventBus.publish({
      kind: 'status-update',
      taskId,
      contextId,
      status: { state: 'working', timestamp: new Date().toISOString() },
      final: false,
    });

    // Simulate a multi-step, long-running process
    for (let i = 0; i < 5; i++) {
      // **Cancellation Checkpoint**
      // Before each step, check if the task has been canceled.
      if (this.cancelledTasks.has(taskId)) {
        console.log(`[Executor] Aborting task ${taskId} due to cancellation.`);

        // Publish the final 'canceled' status.
        const cancelledUpdate: TaskStatusUpdateEvent = {
          kind: 'status-update',
          taskId: taskId,
          contextId: contextId,
          status: { state: 'canceled', timestamp: new Date().toISOString() },
          final: true,
        };
        eventBus.publish(cancelledUpdate);
        eventBus.finished();

        // Clean up and exit.
        this.cancelledTasks.delete(taskId);
        return;
      }

      // Simulate one step of work.
      console.log(`[Executor] Working on step ${i + 1} for task ${taskId}...`);
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }

    console.log(`[Executor] Task ${taskId} finished all steps without cancellation.`);

    // If not canceled, finish the work and publish the completed state.
    const finalUpdate: TaskStatusUpdateEvent = {
      kind: 'status-update',
      taskId,
      contextId,
      status: { state: 'completed', timestamp: new Date().toISOString() },
      final: true,
    };
    eventBus.publish(finalUpdate);
    eventBus.finished();
  }
}
```

## A2A Push Notifications

For very long-running tasks (e.g., lasting minutes, hours, or even days) or when clients cannot or prefer not to maintain persistent connections (like mobile clients or serverless functions), A2A supports asynchronous updates via push notifications. This mechanism allows the A2A Server to actively notify a client-provided webhook when a significant task update occurs.

### Server-Side Configuration

To enable push notifications, your agent card must declare support:

```typescript
const movieAgentCard: AgentCard = {
  // ... other properties
  capabilities: {
    streaming: true,
    pushNotifications: true, // Enable push notifications
    stateTransitionHistory: true,
  },
  // ... rest of agent card
};
```

When creating the `DefaultRequestHandler`, you can optionally provide custom push notification components:

```typescript
import {
  DefaultRequestHandler,
  InMemoryPushNotificationStore,
  DefaultPushNotificationSender,
} from '@a2a-js/sdk/server';

// Optional: Custom push notification store and sender
const pushNotificationStore = new InMemoryPushNotificationStore();
const pushNotificationSender = new DefaultPushNotificationSender(pushNotificationStore, {
  timeout: 5000, // 5 second timeout
  tokenHeaderName: 'X-A2A-Notification-Token', // Custom header name
});

const requestHandler = new DefaultRequestHandler(
  movieAgentCard,
  taskStore,
  agentExecutor,
  undefined, // eventBusManager (optional)
  pushNotificationStore, // custom store
  pushNotificationSender, // custom sender
  undefined // extendedAgentCard (optional)
);
```

### Client-Side Usage

Configure push notifications when sending messages:

```typescript
// Configure push notification for a message
const pushConfig: PushNotificationConfig = {
  id: 'my-notification-config', // Optional, defaults to task ID
  url: 'https://my-app.com/webhook/task-updates',
  token: 'your-auth-token', // Optional authentication token
};

const sendParams: MessageSendParams = {
  message: {
    messageId: uuidv4(),
    role: 'user',
    parts: [{ kind: 'text', text: 'Hello, agent!' }],
    kind: 'message',
  },
  configuration: {
    blocking: true,
    acceptedOutputModes: ['text/plain'],
    pushNotificationConfig: pushConfig, // Add push notification config
  },
};
```

### Webhook Endpoint Implementation

Your webhook endpoint should expect POST requests with the task data:

```typescript
// Example Express.js webhook endpoint
app.post('/webhook/task-updates', (req, res) => {
  const task = req.body; // The complete task object

  // Verify the token if provided
  const token = req.headers['x-a2a-notification-token'];
  if (token !== 'your-auth-token') {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  console.log(`Task ${task.id} status: ${task.status.state}`);

  // Process the task update
  // ...

  res.status(200).json({ received: true });
});
```

## License

This project is licensed under the terms of the [Apache 2.0 License](https://raw.githubusercontent.com/google-a2a/a2a-python/refs/heads/main/LICENSE).

## Contributing

See [CONTRIBUTING.md](https://github.com/google-a2a/a2a-js/blob/main/CONTRIBUTING.md) for contribution guidelines.
