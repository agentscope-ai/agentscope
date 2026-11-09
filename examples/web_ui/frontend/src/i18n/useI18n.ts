import { useTranslation as useI18nextTranslation } from 'react-i18next';

export const useTranslation = () => {
	return useI18nextTranslation('translation');
};
