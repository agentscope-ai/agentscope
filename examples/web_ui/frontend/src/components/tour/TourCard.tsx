import { X } from 'lucide-react';
import { useOnborda, type CardComponentProps } from 'onborda';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';
import { useTranslation } from '@/i18n/useI18n';

export const TourCard = ({
	step,
	currentStep,
	totalSteps,
	nextStep,
	prevStep,
	arrow,
}: CardComponentProps) => {
	const { t } = useTranslation();
	const { closeOnborda } = useOnborda();

	const isFirst = currentStep === 0;
	const isLast = currentStep === totalSteps - 1;

	const handleClose = () => {
		localStorage.setItem('chat_tour_done', '1');
		closeOnborda();
	};

	const handleNext = () => {
		if (isLast) {
			handleClose();
			return;
		}
		nextStep();
	};

	return (
		<>
			{/* Arrow inherits color via `currentColor`; force it to the card's
			    background so the triangle visually merges with the card. */}
			<span style={{ color: 'var(--card)' }}>{arrow}</span>

			<Card size="sm" className="w-80 shadow-lg">
				<CardHeader className="flex flex-row items-start justify-between gap-2">
					<CardTitle>{step.title}</CardTitle>
					<button
						onClick={handleClose}
						className="text-muted-foreground hover:text-foreground -mt-1"
						aria-label={t('tour.skip')}
					>
						<X className="size-4" />
					</button>
				</CardHeader>
				<CardContent className="text-muted-foreground text-sm leading-relaxed">
					{step.content}
				</CardContent>
				<CardFooter className="flex items-center justify-between">
					<span className="text-muted-foreground text-xs">
						{t('tour.step', { current: currentStep + 1, total: totalSteps })}
					</span>
					<div className="flex items-center gap-2">
						{!isFirst && (
							<Button size="sm" variant="ghost" onClick={prevStep}>
								{t('tour.prev')}
							</Button>
						)}
						<Button size="sm" onClick={handleNext}>
							{isLast ? t('tour.finish') : t('tour.next')}
						</Button>
					</div>
				</CardFooter>
			</Card>
		</>
	);
};
