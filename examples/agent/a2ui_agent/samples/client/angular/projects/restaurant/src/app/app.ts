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

import { MessageProcessor, Surface } from '@a2ui/angular';
import { Types } from '@a2ui/lit/0.8';
import { Component, DOCUMENT, inject, signal } from '@angular/core';
import { Client } from './client';

@Component({
  selector: 'app-root',
  templateUrl: './app.html',
  styleUrl: 'app.css',
  imports: [Surface],
})
export class App {
  protected client = inject(Client);
  protected processor = inject(MessageProcessor);
  private document = inject(DOCUMENT);
  private loadingInterval: number | undefined;

  protected loadingTextIndex = signal(0);
  protected hasData = signal(false);
  protected loadingTextLines = [
    'Finding the best spots for you...',
    'Checking reviews...',
    'Looking for open tables...',
    'Almost there...',
  ];

  protected async handleSubmit(event: SubmitEvent) {
    event.preventDefault();

    if (!(event.target instanceof HTMLFormElement)) {
      return;
    }

    const data = new FormData(event.target);
    const body = data.get('body') ?? null;

    if (body) {
      this.startLoadingAnimation();
      const message = body as Types.A2UIClientEventMessage;
      await this.client.makeRequest(message);
      this.hasData.set(true);
      this.stopLoadingAnimation();
    }
  }

  protected toggleTheme(button: HTMLButtonElement) {
    const { colorScheme } = window.getComputedStyle(button);
    const classList = this.document.body.classList;

    if (colorScheme === 'dark') {
      classList.add('light');
      classList.remove('dark');
    } else {
      classList.add('dark');
      classList.remove('light');
    }
  }

  private startLoadingAnimation() {
    this.loadingTextIndex.set(0);

    this.loadingInterval = window.setInterval(() => {
      this.loadingTextIndex.update((prev) => (prev + 1) % this.loadingTextLines.length);
    }, 2000);
  }

  private stopLoadingAnimation() {
    if (this.loadingInterval) {
      clearInterval(this.loadingInterval);
      this.loadingInterval = undefined;
    }
  }
}
