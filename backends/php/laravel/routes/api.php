<?php

use App\Http\Controllers\SocialController;
use Illuminate\Support\Facades\Route;

foreach (['/health/live', '/health/ready', '/health/metrics', '/api/v1/users/me', '/api/v1/feed', '/api/v1/posts/{post_id}', '/api/v1/posts/{post_id}/comments'] as $path) {
    Route::get($path, SocialController::class);
}
foreach (['/api/v1/auth/register', '/api/v1/auth/login', '/api/v1/posts', '/api/v1/posts/{post_id}/comments'] as $path) {
    Route::post($path, SocialController::class);
}
Route::match(['PUT', 'DELETE'], '/api/v1/users/{target_id}/follow', SocialController::class);
Route::match(['PUT', 'DELETE'], '/api/v1/posts/{post_id}/like', SocialController::class);
