<?php

use Illuminate\Http\Request;

require __DIR__.'/../vendor/autoload.php';
App\Support\Protocol::publishTuningReceipt();
(require_once __DIR__.'/../bootstrap/app.php')->handleRequest(Request::capture());
