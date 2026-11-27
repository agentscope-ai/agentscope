import Editor, { loader } from '@monaco-editor/react';
import * as monaco from 'monaco-editor';
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
import CssWorker from 'monaco-editor/esm/vs/language/css/css.worker?worker';
import HtmlWorker from 'monaco-editor/esm/vs/language/html/html.worker?worker';
import JsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker';
import TsWorker from 'monaco-editor/esm/vs/language/typescript/ts.worker?worker';
import 'monaco-editor/min/vs/editor/editor.main.css';
import { useEffect, useId, useMemo, useState } from 'react';

import { useTranslation } from '@/i18n/useI18n';

// This module is loaded only on entering edit mode. All assets stay local.
self.MonacoEnvironment = {
	...self.MonacoEnvironment,
	getWorker(_id, label) {
		switch (label) {
			case 'json':
				return new JsonWorker();
			case 'css':
			case 'scss':
			case 'less':
				return new CssWorker();
			case 'html':
			case 'handlebars':
			case 'razor':
				return new HtmlWorker();
			case 'typescript':
			case 'javascript':
				return new TsWorker();
			default:
				return new EditorWorker();
		}
	},
};
loader.config({ monaco });

interface Props {
	path: string;
	value: string;
	readOnly: boolean;
	onChange: (value: string) => void;
}

export default function ArtifactEditor({ path, value, readOnly, onChange }: Props) {
	const { t } = useTranslation();
	const id = useId();
	const [dark, setDark] = useState(() => document.documentElement.classList.contains('dark'));
	useEffect(() => {
		const root = document.documentElement;
		const observer = new MutationObserver(() => setDark(root.classList.contains('dark')));
		observer.observe(root, { attributes: true, attributeFilter: ['class'] });
		return () => observer.disconnect();
	}, []);
	const language = useMemo(() => {
		const name = path.split(/[/\\]/).pop()?.toLowerCase() ?? '';
		const extension = name.includes('.') ? name.slice(name.lastIndexOf('.')) : '';
		const languages = monaco.languages.getLanguages();
		return (
			languages.find((entry) => entry.filenames?.some((file) => file.toLowerCase() === name))
				?.id ??
			languages.find((entry) => entry.extensions?.includes(extension))?.id ??
			'plaintext'
		);
	}, [path]);
	// Isolate models across sessions and drawer instances, even for equal paths.
	const modelPath = useMemo(
		() =>
			monaco.Uri.from({
				scheme: 'artifact',
				path: `/${id}/${path.split(/[/\\]/).pop() || 'file'}`,
			}).toString(),
		[id, path],
	);

	return (
		<div className="min-h-80 min-w-0 flex-1 overflow-hidden rounded-md border">
			<Editor
				key={modelPath}
				height="100%"
				path={modelPath}
				language={language}
				value={value}
				theme={dark ? 'vs-dark' : 'light'}
				saveViewState={false}
				onChange={(next) => {
					if (!readOnly) onChange(next ?? '');
				}}
				loading={<span role="status">{t('common.loading')}</span>}
				options={{
					ariaLabel: t('artifacts.editor'),
					readOnly,
					automaticLayout: true,
					minimap: { enabled: false },
					fontSize: 13,
					fontFamily: 'var(--font-mono)',
					lineNumbers: 'on',
					scrollBeyondLastLine: false,
					wordWrap: 'on',
					tabSize: 2,
					padding: { top: 12, bottom: 12 },
				}}
			/>
		</div>
	);
}
