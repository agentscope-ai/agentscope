export interface ScheduleEvent {
	id: string;
	title: string;
	date: string; // ISO format: YYYY-MM-DD
	time: string; // 24-hour format: HH:mm
	content: string;
	location?: string;
	attendees?: string[];
}
