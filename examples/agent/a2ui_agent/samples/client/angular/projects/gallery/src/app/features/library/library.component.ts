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

import { Component, ElementRef, ViewChild } from '@angular/core';
import { Surface } from '@a2ui/angular';
import * as v0_8 from '@a2ui/lit/0.8';

@Component({
  selector: 'app-library',
  imports: [Surface],
  templateUrl: './library.html',
  styleUrl: './library.css',
})
export class LibraryComponent {
  @ViewChild('dialog') dialog!: ElementRef<HTMLDialogElement>;
  selectedBlock: { name: string; surface: v0_8.Types.Surface } | null = null;
  activeSection = '';
  showJsonId: string | null = null;

  openDialog(block: { name: string; surface: v0_8.Types.Surface }) {
    this.selectedBlock = block;
    this.dialog.nativeElement.showModal();
  }

  closeDialog() {
    this.dialog.nativeElement.close();
  }

  scrollTo(name: string) {
    this.activeSection = name;
    const element = document.getElementById('section-' + name);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  onScroll(event: Event) {
    const container = event.target as HTMLElement;
    const sections = container.querySelectorAll('.component-section');

    let current = '';
    const containerTop = container.scrollTop;

    // Find the section that is closest to the top of the container
    // We add a small offset (e.g. 100px) so it activates slightly before reaching the very top
    for (let i = 0; i < sections.length; i++) {
      const section = sections[i] as HTMLElement;
      const sectionTop = section.offsetTop - container.offsetTop;

      if (sectionTop <= containerTop + 100) {
        // This section is above or near the top, so it's a candidate
        // Since we iterate in order, the last one matching this condition is the current one
        const id = section.getAttribute('id');
        if (id) {
          current = id.replace('section-', '');
        }
      }
    }

    if (current && current !== this.activeSection) {
      this.activeSection = current;
    }
  }

  toggleJson(name: string) {
    this.showJsonId = this.showJsonId === name ? null : name;
  }

  getJson(surface: v0_8.Types.Surface): string {
    return JSON.stringify(
      surface,
      (key, value) => {
        if (key === 'rootComponentId' || key === 'dataModel' || key === 'styles') return undefined;
        if (value instanceof Map) return Object.fromEntries(value.entries());
        return value;
      },
      2,
    );
  }

  blocks = [
    {
      name: 'Card',
      tag: 'Layout',
      surface: this.createSingleComponentSurface('Card', {
        child: this.createComponent('Text', { text: { literalString: 'Content inside a card' } }),
      }),
    },
    {
      name: 'Column',
      tag: 'Layout',
      surface: this.createSingleComponentSurface('Column', {
        children: [
          this.createComponent('Text', { text: { literalString: 'Item 1' } }),
          this.createComponent('Text', { text: { literalString: 'Item 2' } }),
          this.createComponent('Text', { text: { literalString: 'Item 3' } }),
        ],
        alignment: 'center',
        distribution: 'space-around',
      }),
    },
    {
      name: 'Divider',
      tag: 'Layout',
      surface: this.createSingleComponentSurface('Column', {
        children: [
          this.createComponent('Text', { text: { literalString: 'Above Divider' } }),
          this.createComponent('Divider', {}),
          this.createComponent('Text', { text: { literalString: 'Below Divider' } }),
        ],
      }),
    },
    {
      name: 'List',
      tag: 'Layout',
      surface: this.createSingleComponentSurface('List', {
        children: [
          this.createComponent('Text', { text: { literalString: 'List Item 1' } }),
          this.createComponent('Text', { text: { literalString: 'List Item 2' } }),
          this.createComponent('Text', { text: { literalString: 'List Item 3' } }),
        ],
        direction: 'vertical',
      }),
    },
    {
      name: 'Modal',
      tag: 'Layout',
      surface: this.createSingleComponentSurface('Modal', {
        entryPointChild: this.createComponent('Button', {
          action: { type: 'none' },
          child: this.createComponent('Text', { text: { literalString: 'Open Modal' } }),
        }),
        contentChild: this.createComponent('Card', {
          child: this.createComponent('Text', {
            text: { literalString: 'This is the modal content.' },
          }),
        }),
      }),
    },
    {
      name: 'Row',
      tag: 'Layout',
      surface: this.createSingleComponentSurface('Row', {
        children: [
          this.createComponent('Text', { text: { literalString: 'Left' } }),
          this.createComponent('Text', { text: { literalString: 'Center' } }),
          this.createComponent('Text', { text: { literalString: 'Right' } }),
        ],
        alignment: 'center',
        distribution: 'space-between',
      }),
    },
    {
      name: 'Tabs',
      tag: 'Layout',
      surface: this.createSingleComponentSurface('Tabs', {
        tabItems: [
          {
            title: { literalString: 'Tab 1' },
            child: this.createComponent('Text', { text: { literalString: 'Content for Tab 1' } }),
          },
          {
            title: { literalString: 'Tab 2' },
            child: this.createComponent('Text', { text: { literalString: 'Content for Tab 2' } }),
          },
        ],
      }),
    },
    {
      name: 'Text',
      tag: 'Layout',
      surface: this.createSingleComponentSurface('Column', {
        children: [
          this.createComponent('Heading', { text: { literalString: 'Heading Text' } }),
          this.createComponent('Text', { text: { literalString: 'Standard body text.' } }),
          this.createComponent('Text', {
            text: { literalString: 'Caption text' },
            usageHint: 'caption',
          }),
        ],
      }),
    },

    {
      name: 'AudioPlayer',
      tag: 'Media',
      surface: this.createSingleComponentSurface('AudioPlayer', {
        url: { literalString: 'https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3' },
      }),
    },
    {
      name: 'Icon',
      tag: 'Media',
      surface: this.createSingleComponentSurface('Row', {
        children: [
          this.createComponent('Icon', { name: { literalString: 'home' } }),
          this.createComponent('Icon', { name: { literalString: 'favorite' } }),
          this.createComponent('Icon', { name: { literalString: 'settings' } }),
        ],
        distribution: 'space-around',
      }),
    },
    {
      name: 'Image',
      tag: 'Media',
      surface: this.createSingleComponentSurface('Image', {
        url: { literalString: 'https://picsum.photos/id/10/300/200' },
      }),
    },
    {
      name: 'Video',
      tag: 'Media',
      surface: this.createSingleComponentSurface('Video', {
        url: {
          literalString:
            'http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4',
        },
      }),
    },
    {
      name: 'Button',
      tag: 'Inputs',
      surface: this.createSingleComponentSurface('Row', {
        children: [
          this.createComponent('Button', {
            label: { literalString: 'Primary' },
            action: { type: 'click' },
            child: this.createComponent('Text', { text: { literalString: 'Primary' } }),
          }),
          this.createComponent('Button', {
            label: { literalString: 'Secondary' },
            action: { type: 'click' },
            child: this.createComponent('Text', { text: { literalString: 'Secondary' } }),
          }),
        ],
        distribution: 'space-around',
      }),
    },
    {
      name: 'CheckBox',
      tag: 'Inputs',
      surface: this.createSingleComponentSurface('Column', {
        children: [
          this.createComponent('CheckBox', {
            label: { literalString: 'Unchecked' },
            value: { literalBoolean: false },
          }),
          this.createComponent('CheckBox', {
            label: { literalString: 'Checked' },
            value: { literalBoolean: true },
          }),
        ],
      }),
    },
    {
      name: 'DateTimeInput',
      tag: 'Inputs',
      surface: this.createSingleComponentSurface('Column', {
        children: [
          this.createComponent('DateTimeInput', {
            enableDate: true,
            enableTime: false,
            value: { literalString: '2025-12-09' },
          }),
          this.createComponent('DateTimeInput', {
            enableDate: true,
            enableTime: true,
            value: { literalString: '2025-12-09T12:00:00' },
          }),
        ],
      }),
    },
    {
      name: 'MultipleChoice',
      tag: 'Inputs',
      surface: this.createSingleComponentSurface('MultipleChoice', {
        options: [
          { value: 'opt1', label: { literalString: 'Option 1' } },
          { value: 'opt2', label: { literalString: 'Option 2' } },
          { value: 'opt3', label: { literalString: 'Option 3' } },
        ],
        selections: { literalString: 'opt1' },
      }),
    },
    {
      name: 'Slider',
      tag: 'Inputs',
      surface: this.createSingleComponentSurface('Slider', {
        value: { literalNumber: 50 },
        minValue: 0,
        maxValue: 100,
      }),
    },
    {
      name: 'TextField',
      tag: 'Inputs',
      surface: this.createSingleComponentSurface('Column', {
        children: [
          this.createComponent('TextField', {
            label: { literalString: 'Standard Input' },
            text: { literalString: 'Some text' },
          }),
          this.createComponent('TextField', {
            label: { literalString: 'Password' },
            type: 'password',
            text: { literalString: '' },
          }),
        ],
      }),
    },
  ];

  categories = [
    {
      name: 'Layout',
      samples: [
        {
          name: 'Card',
          surface: this.createSingleComponentSurface('Card', {
            child: this.createComponent('Text', {
              text: { literalString: 'Content inside a card' },
            }),
          }),
        },
        {
          name: 'Column',
          surface: this.createSingleComponentSurface('Column', {
            children: [
              this.createComponent('Text', { text: { literalString: 'Item 1' } }),
              this.createComponent('Text', { text: { literalString: 'Item 2' } }),
              this.createComponent('Text', { text: { literalString: 'Item 3' } }),
            ],
            alignment: 'center',
            distribution: 'space-around',
          }),
        },
        {
          name: 'Divider',
          surface: this.createSingleComponentSurface('Column', {
            children: [
              this.createComponent('Text', { text: { literalString: 'Above Divider' } }),
              this.createComponent('Divider', {}),
              this.createComponent('Text', { text: { literalString: 'Below Divider' } }),
            ],
          }),
        },
        {
          name: 'List',
          surface: this.createSingleComponentSurface('List', {
            children: [
              this.createComponent('Text', { text: { literalString: 'List Item 1' } }),
              this.createComponent('Text', { text: { literalString: 'List Item 2' } }),
              this.createComponent('Text', { text: { literalString: 'List Item 3' } }),
            ],
            direction: 'vertical',
          }),
        },
        {
          name: 'Modal',
          surface: this.createSingleComponentSurface('Modal', {
            entryPointChild: this.createComponent('Button', {
              action: { type: 'none' },
              child: this.createComponent('Text', { text: { literalString: 'Open Modal' } }),
            }),
            contentChild: this.createComponent('Card', {
              child: this.createComponent('Text', {
                text: { literalString: 'This is the modal content.' },
              }),
            }),
          }),
        },
        {
          name: 'Row',
          surface: this.createSingleComponentSurface('Row', {
            children: [
              this.createComponent('Text', { text: { literalString: 'Left' } }),
              this.createComponent('Text', { text: { literalString: 'Center' } }),
              this.createComponent('Text', { text: { literalString: 'Right' } }),
            ],
            alignment: 'center',
            distribution: 'space-between',
          }),
        },
        {
          name: 'Tabs',
          surface: this.createSingleComponentSurface('Tabs', {
            tabItems: [
              {
                title: { literalString: 'Tab 1' },
                child: this.createComponent('Text', {
                  text: { literalString: 'Content for Tab 1' },
                }),
              },
              {
                title: { literalString: 'Tab 2' },
                child: this.createComponent('Text', {
                  text: { literalString: 'Content for Tab 2' },
                }),
              },
            ],
          }),
        },
        {
          name: 'Text',
          surface: this.createSingleComponentSurface('Column', {
            children: [
              this.createComponent('Heading', { text: { literalString: 'Heading Text' } }),
              this.createComponent('Text', { text: { literalString: 'Standard body text.' } }),
              this.createComponent('Text', {
                text: { literalString: 'Caption text' },
                usageHint: 'caption',
              }),
            ],
          }),
        },
      ],
    },
    {
      name: 'Media',
      samples: [
        {
          name: 'AudioPlayer',
          surface: this.createSingleComponentSurface('AudioPlayer', {
            url: { literalString: 'https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3' },
          }),
        },
        {
          name: 'Icon',
          surface: this.createSingleComponentSurface('Row', {
            children: [
              this.createComponent('Icon', { name: { literalString: 'home' } }),
              this.createComponent('Icon', { name: { literalString: 'favorite' } }),
              this.createComponent('Icon', { name: { literalString: 'settings' } }),
            ],
            distribution: 'space-around',
          }),
        },
        {
          name: 'Image',
          surface: this.createSingleComponentSurface('Image', {
            url: { literalString: 'https://picsum.photos/id/10/300/200' },
          }),
        },
        {
          name: 'Video',
          surface: this.createSingleComponentSurface('Video', {
            url: {
              literalString:
                'http://commondatastorage.googleapis.com/gtv-videos-bucket/sample/BigBuckBunny.mp4',
            },
          }),
        },
      ],
    },
    {
      name: 'Inputs',
      samples: [
        {
          name: 'Button',
          surface: this.createSingleComponentSurface('Row', {
            children: [
              this.createComponent('Button', {
                label: { literalString: 'Primary' },
                action: { type: 'click' },
                child: this.createComponent('Text', { text: { literalString: 'Primary' } }),
              }),
              this.createComponent('Button', {
                label: { literalString: 'Secondary' },
                action: { type: 'click' },
                child: this.createComponent('Text', { text: { literalString: 'Secondary' } }),
              }),
            ],
            distribution: 'space-around',
          }),
        },
        {
          name: 'CheckBox',
          surface: this.createSingleComponentSurface('Column', {
            children: [
              this.createComponent('CheckBox', {
                label: { literalString: 'Unchecked' },
                value: { literalBoolean: false },
              }),
              this.createComponent('CheckBox', {
                label: { literalString: 'Checked' },
                value: { literalBoolean: true },
              }),
            ],
          }),
        },
        {
          name: 'DateTimeInput',
          surface: this.createSingleComponentSurface('Column', {
            children: [
              this.createComponent('DateTimeInput', {
                enableDate: true,
                enableTime: false,
                value: { literalString: '2025-12-09' },
              }),
              this.createComponent('DateTimeInput', {
                enableDate: true,
                enableTime: true,
                value: { literalString: '2025-12-09T12:00:00' },
              }),
            ],
          }),
        },
        {
          name: 'MultipleChoice',
          surface: this.createSingleComponentSurface('MultipleChoice', {
            options: [
              { value: 'opt1', label: { literalString: 'Option 1' } },
              { value: 'opt2', label: { literalString: 'Option 2' } },
              { value: 'opt3', label: { literalString: 'Option 3' } },
            ],
            selections: { literalString: 'opt1' },
          }),
        },
        {
          name: 'Slider',
          surface: this.createSingleComponentSurface('Slider', {
            value: { literalNumber: 50 },
            minValue: 0,
            maxValue: 100,
          }),
        },
        {
          name: 'TextField',
          surface: this.createSingleComponentSurface('Column', {
            children: [
              this.createComponent('TextField', {
                label: { literalString: 'Standard Input' },
                text: { literalString: 'Some text' },
              }),
              this.createComponent('TextField', {
                label: { literalString: 'Password' },
                type: 'password',
                text: { literalString: '' },
              }),
            ],
          }),
        },
      ],
    },
  ];

  private createSingleComponentSurface(type: string, properties: any): v0_8.Types.Surface {
    const rootId = 'root';

    return {
      rootComponentId: rootId,
      dataModel: new Map(),
      styles: {},
      componentTree: {
        id: rootId,
        type: type,
        properties: properties,
      } as any,
      components: new Map(),
    };
  }

  private createComponent(type: string, properties: any): any {
    return {
      id: 'generated-' + Math.random().toString(36).substr(2, 9), // ID will be overridden by key in map usually, or ignored if inline
      type: type,
      properties: properties,
    };
  }
}
