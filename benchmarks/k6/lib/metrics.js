import {Counter,Trend} from 'k6/metrics';
export const attempted=new Counter('operations_attempted');
export const completed=new Counter('operations_completed');
export const succeeded=new Counter('operations_succeeded');
export const failed=new Counter('operations_failed');
export const duration=new Trend('operation_duration',true);
