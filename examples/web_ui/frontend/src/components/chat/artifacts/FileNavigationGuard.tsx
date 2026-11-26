import { useEffect } from 'react';
import { useBlocker } from 'react-router-dom';

export function FileNavigationGuard({
	blocked,
	confirmLeave,
}: {
	blocked: boolean;
	confirmLeave: () => boolean;
}) {
	const blocker = useBlocker(blocked);
	useEffect(() => {
		if (blocker.state !== 'blocked') return;
		if (confirmLeave()) blocker.proceed();
		else blocker.reset();
	}, [blocker, confirmLeave]);
	return null;
}
