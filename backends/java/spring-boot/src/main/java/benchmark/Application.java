package benchmark;

import com.zaxxer.hikari.*;
import java.sql.*;
import java.util.concurrent.atomic.*;
import javax.sql.DataSource;
import org.springframework.boot.*;
import org.springframework.boot.autoconfigure.*;
import org.springframework.context.annotation.*;
import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
import org.springframework.jdbc.datasource.DataSourceTransactionManager;
import org.springframework.jdbc.datasource.DelegatingDataSource;
import org.springframework.transaction.support.TransactionTemplate;

@SpringBootApplication
public class Application {
  static final LongAdder ACQUIRES = new LongAdder();
  static final LongAdder NANOS = new LongAdder();

  public static void main(String[] args) {
    SpringApplication.run(Application.class, args);
  }

  @Bean
  Protocol protocol() {
    return new Protocol(
        System.getenv("JWT_SECRET"),
        Integer.parseInt(System.getenv().getOrDefault("JWT_TTL_SECONDS", "3600")));
  }

  @Bean
  DataSource dataSource() throws Exception {
    var parsed = DatabaseConfig.parse(System.getenv("DATABASE_URL"), System.getenv("MODE"));
    var cfg = new HikariConfig();
    cfg.setJdbcUrl(parsed.jdbcUrl());
    cfg.setUsername(parsed.username());
    cfg.setPassword(parsed.password());
    cfg.setMaximumPoolSize(20);
    cfg.setMinimumIdle(2);
    cfg.setConnectionTimeout(5000);
    cfg.setPoolName("social-api");
    var hikari = new HikariDataSource(cfg);
    return new DelegatingDataSource(hikari) {
      @Override
      public Connection getConnection() throws SQLException {
        long start = System.nanoTime();
        var c = super.getConnection();
        ACQUIRES.increment();
        NANOS.add(System.nanoTime() - start);
        return c;
      }
    };
  }

  @Bean
  NamedParameterJdbcTemplate jdbc(DataSource db) {
    return new NamedParameterJdbcTemplate(db);
  }

  @Bean
  TransactionTemplate transaction(DataSource db) {
    return new TransactionTemplate(new DataSourceTransactionManager(db));
  }
}
