import { useMemo } from 'react';

import { useTranslation } from '@/i18n/useI18n.ts';
import type { TimeUnits } from '@/utils/common';

/**
 * Translated unit suffixes for {@link formatTime}.
 *
 * `formatTime` is a pure util and cannot reach i18n itself, so a caller
 * that renders **into a localised sentence** has to hand it these —
 * otherwise a Chinese "{{ago}}前更新" comes out as "2mon前更新".
 *
 * A duration badge that stands on its own is the opposite case and
 * should keep the default English units: it reads as a compact figure
 * rather than as prose, and translating it only makes it wider.
 */
export function useTimeUnits(): TimeUnits {
	const { t } = useTranslation();
	return useMemo(
		() => ({
			s: t('common.timeUnits.s'),
			m: t('common.timeUnits.m'),
			h: t('common.timeUnits.h'),
			d: t('common.timeUnits.d'),
			mon: t('common.timeUnits.mon'),
			y: t('common.timeUnits.y'),
		}),
		[t],
	);
}
