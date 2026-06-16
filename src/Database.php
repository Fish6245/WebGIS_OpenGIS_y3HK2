<?php
declare(strict_types=1);
// database.php

class Database
{
    public static function conn(): ?PDO
    {
        static $pdo = null;

        if ($pdo instanceof PDO) {
            return $pdo;
        }

        $host = env_value('DB_HOST', '127.0.0.1');
        $port = env_value('DB_PORT', '5432');
        $name = env_value('DB_NAME', 'gis_db');
        $user = env_value('DB_USER', 'postgres');
        $pass = env_value('DB_PASSWORD', '');

        try {
            $dsn = "pgsql:host={$host};port={$port};dbname={$name}";
            $pdo = new PDO($dsn, $user, $pass, [
                PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
                PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
            ]);
            return $pdo;
        } catch (Throwable $e) {
            return null;
        }
    }
}