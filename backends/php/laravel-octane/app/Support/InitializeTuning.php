<?php

namespace App\Support;

use Laravel\Octane\Events\WorkerStarting;

final class InitializeTuning
{
    public function handle(WorkerStarting $event): void
    {
        Protocol::publishTuningReceipt();
    }
}
