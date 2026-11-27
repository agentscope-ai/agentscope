import { useEffect, useEffectEvent, useRef, useState } from 'react';

import type { OfficeKind } from './artifacts';
import { useTranslation } from '@/i18n/useI18n';

export type OfficePreviewError = 'officeEncrypted' | 'officeInvalid' | 'large' | 'error';

async function viewerFor(kind: OfficeKind) {
	switch (kind) {
		case 'docx':
			return (await import('@silurus/ooxml/docx')).DocxScrollViewer;
		case 'xlsx':
			return (await import('@silurus/ooxml/xlsx')).XlsxViewer;
		case 'pptx':
			return (await import('@silurus/ooxml/pptx')).PptxScrollViewer;
	}
}

function previewError(error: unknown): OfficePreviewError {
	if (error instanceof Error) {
		if (
			'code' in error &&
			['encrypted', 'invalid-password', 'unsupported-encryption'].includes(String(error.code))
		) {
			return 'officeEncrypted';
		}
		if (
			error.name === 'OoxmlResourceLimitError' ||
			error.name === 'OoxmlDecodedImageLimitError'
		) {
			return 'large';
		}
	}
	return 'officeInvalid';
}

export function OfficePreview({
	kind,
	file,
	name,
	onError,
}: {
	kind: OfficeKind;
	file: Blob;
	name: string;
	onError: (status: OfficePreviewError) => void;
}) {
	const { t } = useTranslation();
	const host = useRef<HTMLDivElement>(null);
	const [loading, setLoading] = useState(true);
	const reportError = useEffectEvent(onError);

	useEffect(() => {
		const container = host.current;
		if (!container) return;
		let active = true;
		let viewer: { destroy(): void } | undefined;
		const fail = (error: unknown) => {
			if (active) reportError(previewError(error));
		};
		const load = async () => {
			const [Viewer, bytes] = await Promise.all([viewerFor(kind), file.arrayBuffer()]);
			if (!active) return;
			const instance = new Viewer(container, {
				enableHyperlinks: false,
				enableMediaPlayback: false,
				refitOnResize: true,
				onError: fail,
			});
			viewer = instance;
			try {
				await instance.load(bytes);
				if (active) setLoading(false);
			} catch (error) {
				fail(error);
			}
		};
		void load().catch(() => {
			if (active) reportError('error');
		});
		return () => {
			active = false;
			viewer?.destroy();
		};
	}, [file, kind]);

	return (
		<>
			{loading && <span role="status">{t('common.loading')}</span>}
			<div
				ref={host}
				role="region"
				aria-label={name}
				aria-busy={loading}
				className="relative min-h-64 w-full min-w-0 flex-1 overflow-hidden"
			/>
		</>
	);
}
