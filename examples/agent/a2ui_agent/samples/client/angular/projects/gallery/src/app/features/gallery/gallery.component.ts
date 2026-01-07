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
import { CommonModule } from '@angular/common';
import { Surface } from '@a2ui/angular';
import * as v0_8 from '@a2ui/lit/0.8';

interface GallerySample {
  id: string;
  title: string;
  description: string;
  surface: v0_8.Types.Surface;
}

@Component({
  selector: 'app-gallery',
  imports: [CommonModule, Surface],
  templateUrl: './gallery.html',
  styleUrl: './gallery.css',
})
export class GalleryComponent {
  @ViewChild('dialog') dialog!: ElementRef<HTMLDialogElement>;
  selectedSample: GallerySample | null = null;
  activeSection = 'welcome';
  showJsonId: string | null = null;

  samples: GallerySample[] = [
    {
      id: 'photo-list',
      title: 'List of items',
      description: 'List of items with images',
      surface: this.createSingleComponentSurface('Card', {
        child: this.createComponent('Column', {
          children: [
            this.createComponent('Row', {
              children: [
                this.createComponent('Image', {
                  url: { literalString: 'https://picsum.photos/id/11/300/300' },
                }),
                this.createComponent('Column', {
                  children: [
                    this.createComponent('Text', {
                      text: {
                        literalString: 'A misty, serene natural landscape.',
                      },
                    }),
                  ],
                }),
              ],
            }),
            this.createComponent('Row', {
              children: [
                this.createComponent('Image', {
                  url: { literalString: 'https://picsum.photos/id/12/300/300' },
                }),
                this.createComponent('Column', {
                  children: [
                    this.createComponent('Text', {
                      text: {
                        literalString:
                          'A river flows through marsh toward hazy, forested mountains.',
                      },
                    }),
                  ],
                }),
              ],
            }),
            this.createComponent('Row', {
              children: [
                this.createComponent('Image', {
                  url: { literalString: 'https://picsum.photos/id/13/300/300' },
                }),
                this.createComponent('Text', {
                  text: {
                    literalString:
                      'Large dark rocks overlook sandy beach and ocean with distant islands.',
                  },
                }),
              ],
            }),
          ],
        }),
      }),
    },
    {
      id: 'welcome',
      title: 'Welcome Card',
      description: 'A simple welcome card with an image and text.',
      surface: this.createSingleComponentSurface('Card', {
        child: this.createComponent('Column', {
          children: [
            this.createComponent('Image', {
              url: { literalString: 'https://picsum.photos/id/10/600/300' },
            }),
            this.createComponent('Text', {
              text: {
                literalString:
                  'Explore the possibilities of A2UI components with this interactive gallery.',
              },
            }),
            this.createComponent('Button', {
              action: { type: 'submit' },
              child: this.createComponent('Text', { text: { literalString: 'Get Started' } }),
            }),
          ],
          alignment: 'center',
        }),
      }),
    },
    {
      id: 'form',
      title: 'Contact Form',
      description: 'A sample contact form with validation.',
      surface: this.createSingleComponentSurface('Card', {
        child: this.createComponent('Column', {
          children: [
            this.createComponent('Row', {
              children: [
                this.createComponent('TextField', {
                  label: { literalString: 'Name' },
                  type: 'text',
                  text: { literalString: '' },
                }),
              ],
            }),
            this.createComponent('Row', {
              children: [
                this.createComponent('TextField', {
                  label: { literalString: 'Email Address' },
                  type: 'email',
                  text: { literalString: '' },
                }),
              ],
            }),
            this.createComponent('Row', {
              children: [
                this.createComponent('TextField', {
                  label: { literalString: 'Message' },
                  text: { literalString: '' },
                }),
              ],
            }),
            this.createComponent('Button', {
              action: { type: 'submit' },
              child: this.createComponent('Text', { text: { literalString: 'Send Message' } }),
            }),
          ],
        }),
      }),
    },
  ];

  openDialog(sample: GallerySample) {
    this.selectedSample = sample;
    this.dialog.nativeElement.showModal();
  }

  closeDialog() {
    this.dialog.nativeElement.close();
  }

  scrollTo(id: string) {
    this.activeSection = id;
    const element = document.getElementById('section-' + id);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  onScroll(event: Event) {
    const container = event.target as HTMLElement;
    const sections = container.querySelectorAll('.component-section');

    let current = '';
    const containerTop = container.scrollTop;

    for (let i = 0; i < sections.length; i++) {
      const section = sections[i] as HTMLElement;
      const sectionTop = section.offsetTop - container.offsetTop;

      if (sectionTop <= containerTop + 100) {
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

  toggleJson(id: string) {
    this.showJsonId = this.showJsonId === id ? null : id;
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
      id: 'generated-' + Math.random().toString(36).substr(2, 9),
      type: type,
      properties: properties,
    };
  }
}
