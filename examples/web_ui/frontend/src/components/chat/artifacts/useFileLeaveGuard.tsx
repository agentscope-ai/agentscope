import { useCallback, useContext, useEffect, useState } from 'react';
import { UNSAFE_DataRouterContext } from 'react-router-dom';
import { toast } from 'sonner';

import { FileNavigationGuard } from './FileNavigationGuard';
import { useTranslation } from '@/i18n/useI18n';

export interface FileEditState {
	dirty: boolean;
	saving: boolean;
}

/** Guard both drawer actions and navigation out of the active session. */
export function useFileLeaveGuard() {
	const { t } = useTranslation();
	const [state, setState] = useState<FileEditState>({ dirty: false, saving: false });
	const router = useContext(UNSAFE_DataRouterContext);
	const confirmLeave = useCallback(() => {
		if (state.saving) {
			toast.info(t('artifacts.savingWait'));
			return false;
		}
		return !state.dirty || window.confirm(t('artifacts.discardConfirm'));
	}, [state, t]);

	useEffect(() => {
		if (!state.dirty && !state.saving) return;
		const beforeUnload = (event: BeforeUnloadEvent) => {
			event.preventDefault();
			event.returnValue = '';
		};
		window.addEventListener('beforeunload', beforeUnload);
		return () => window.removeEventListener('beforeunload', beforeUnload);
	}, [state]);

	return {
		setEditState: setState,
		confirmLeave,
		navigationGuard: router ? (
			<FileNavigationGuard
				blocked={state.dirty || state.saving}
				confirmLeave={confirmLeave}
			/>
		) : null,
	};
}
