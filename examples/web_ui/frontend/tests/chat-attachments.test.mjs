import assert from 'node:assert/strict';
import test from 'node:test';
import { UserMsg, getTextContent } from '@agentscope-ai/agentscope/message';

import {
	getChatAttachmentInputTypes,
	readChatTextAttachment,
} from '../src/lib/chat-attachments.ts';

test('a text-only model can select Markdown by extension even when its MIME is unknown', () => {
	const accepted = getChatAttachmentInputTypes(['text/plain', 'application/x-thinking']);
	assert.ok(accepted.includes('.md'));
	assert.ok(accepted.includes('.markdown'));
	assert.ok(accepted.includes('.txt'));
	assert.ok(accepted.includes('text/markdown'));
	assert.ok(!accepted.includes('application/x-thinking'));
	assert.ok(!accepted.includes('application/pdf'));
});

test('media types remain available without giving binary files text support', () => {
	const accepted = getChatAttachmentInputTypes(['text/plain', 'image/png', 'application/pdf']);
	assert.ok(accepted.includes('image/png'));
	assert.ok(accepted.includes('application/pdf'));
	assert.equal(new Set(accepted).size, accepted.length);
	assert.deepEqual(getChatAttachmentInputTypes([]), []);
	assert.deepEqual(getChatAttachmentInputTypes(['image/png']), ['image/png']);
	assert.ok(getChatAttachmentInputTypes(['text/*']).includes('.md'));
});

for (const type of ['text/markdown', 'text/x-markdown', '', 'application/octet-stream']) {
	test(`Markdown content is sent as text for MIME ${type || '(empty)'}`, async () => {
		const content = '# 需求\n\n- 保留 **Markdown**、空行与中文。\n';
		const file = new File([content], '需求.md', { type });
		const block = await readChatTextAttachment(file);
		assert.equal(block.type, 'text');
		assert.equal(block.text, `[File: 需求.md]\n${content}`);
		assert.equal(block.source, undefined);
	});
}

test('uppercase and long Markdown extensions are recognized', async () => {
	for (const name of ['NOTES.MD', 'Notes.Markdown']) {
		const block = await readChatTextAttachment(new File(['body'], name));
		assert.equal(block.text, `[File: ${name}]\nbody`);
	}
});

test('a native file path does not replace the actual Markdown contents', async () => {
	const file = new File(['Actual file contents.'], 'notes.md');
	Object.defineProperty(file, 'path', { value: '/example/notes.md' });
	const block = await readChatTextAttachment(file);
	assert.equal(block.type, 'text');
	assert.equal(block.text, '[File: notes.md]\nActual file contents.');
	assert.equal(block.source, undefined);
});

test('plain text and empty files keep the existing text message format', async () => {
	assert.equal(
		(await readChatTextAttachment(new File(['text'], 'notes.txt', { type: 'text/plain' })))
			.text,
		'[File: notes.txt]\ntext',
	);
	assert.equal(
		(await readChatTextAttachment(new File([], 'empty.md'))).text,
		'[File: empty.md]\n',
	);
});

test('PDF, Office and misleading suffixes do not enter the text reader', async () => {
	for (const [name, type] of [
		['document.pdf', 'application/pdf'],
		[
			'document.docx',
			'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
		],
		['notes.md.exe', 'application/octet-stream'],
	]) {
		const file = {
			name,
			type,
			text: async () => assert.fail('Binary content must not be read as text'),
		};
		assert.equal(await readChatTextAttachment(file), null);
	}
});

test('empty-MIME text files and Markdown MIME parameters are supported', async () => {
	assert.equal(
		(await readChatTextAttachment(new File(['text'], 'NOTES.TXT'))).text,
		'[File: NOTES.TXT]\ntext',
	);
	const block = await readChatTextAttachment({
		name: 'notes',
		type: 'Text/Markdown; charset=utf-8',
		text: async () => '正文',
	});
	assert.equal(block.text, '[File: notes]\n正文');
});

test('the SDK user message contains the Markdown body for attachment-only and combined inputs', async () => {
	const body = '# 需求\n\n```sql\nselect 1;\n```\nMD_ATTACHMENT_MARKER';
	const block = await readChatTextAttachment(new File([body], 'requirements.md'));
	for (const prompt of ['', '请分析附件']) {
		const content = prompt
			? [{ ...block, id: crypto.randomUUID(), text: prompt }, block]
			: [block];
		const message = UserMsg({ name: 'user', content });
		const restored = JSON.parse(JSON.stringify(message));
		assert.ok(getTextContent(restored).includes(body));
		assert.ok(
			restored.content.every((item) => item.type === 'text' && item.source === undefined),
		);
	}
});

test('file read failures reject instead of creating an empty successful attachment', async () => {
	await assert.rejects(
		readChatTextAttachment({
			name: 'unreadable.md',
			type: '',
			text: async () => {
				throw new Error('Unable to read the file');
			},
		}),
		/Unable to read the file/,
	);
});
