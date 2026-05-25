import { useOnborda } from 'onborda';
import { useEffect, useRef } from 'react';

import { CHAT_TOUR_NAME } from './chatTourSteps';

interface Props {
	agentsCount: number;
	sessionsCount: number;
	/** Force-open the sidebar so #tour-create-session exists in the DOM. */
	onEnsureSidebarOpen?: () => void;
}

const TOUR_DONE_KEY = 'chat_tour_done';
const FORCE_TOUR_KEY = 'force_tour';

export const ChatTourController = ({ agentsCount, sessionsCount, onEnsureSidebarOpen }: Props) => {
	const { currentStep, currentTour, setCurrentStep, startOnborda } = useOnborda();
	const startCountsRef = useRef({ agents: agentsCount, sessions: sessionsCount });
	const startedRef = useRef(false);

	// Auto-start on mount: first-time visitors, or manual trigger via sessionStorage.
	useEffect(() => {
		if (startedRef.current) return;
		const force = sessionStorage.getItem(FORCE_TOUR_KEY) === '1';
		const done = localStorage.getItem(TOUR_DONE_KEY) === '1';
		if (force) sessionStorage.removeItem(FORCE_TOUR_KEY);
		if (!force && done) return;
		startedRef.current = true;
		onEnsureSidebarOpen?.();
		// Defer one tick so target elements are mounted.
		const id = window.setTimeout(() => startOnborda(CHAT_TOUR_NAME), 300);
		return () => window.clearTimeout(id);
	}, [onEnsureSidebarOpen, startOnborda]);

	// Snapshot the agents/sessions count when entering each step so we can
	// detect "user just created one" rather than "they already had some."
	useEffect(() => {
		if (currentTour !== CHAT_TOUR_NAME) return;
		if (currentStep === 0) startCountsRef.current.agents = agentsCount;
		if (currentStep === 1) startCountsRef.current.sessions = sessionsCount;
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [currentStep, currentTour]);

	// Step 0 → 1: agent created
	useEffect(() => {
		if (currentTour !== CHAT_TOUR_NAME || currentStep !== 0) return;
		if (agentsCount > startCountsRef.current.agents) setCurrentStep(1);
	}, [agentsCount, currentStep, currentTour, setCurrentStep]);

	// Step 1 → 2: session created
	useEffect(() => {
		if (currentTour !== CHAT_TOUR_NAME || currentStep !== 1) return;
		if (sessionsCount > startCountsRef.current.sessions) setCurrentStep(2);
	}, [sessionsCount, currentStep, currentTour, setCurrentStep]);

	// Mark tour as done when finished — triggered when the user reaches the last step
	// and the cards's Finish button calls closeOnborda (which sets the flag inside TourCard).
	// Nothing else to do here.

	return null;
};
